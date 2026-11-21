import re
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import CITEXT, INET
from sqlalchemy.ext.compiler import compiles

import app.models  # noqa: F401
from app.admin_panel.auth import get_session_admin_user
from app.core.security import get_password_hash, hash_token
from app.db.session import Base, get_db
from app.main import app
from app.models.admin_audit_log import AdminAuditLog
from app.models.artist_application import ArtistApplication
from app.models.artist_profile import ArtistProfile
from app.models.artwork import Artwork, ArtworkStatus, ListingType
from app.models.refresh_token import RefreshToken
from app.models.user import ArtistStatus, User
from app.services import user_admin as svc


@compiles(CITEXT, "sqlite")
def _citext_sqlite(type_, compiler, **kw):
    return "TEXT COLLATE NOCASE"


@compiles(INET, "sqlite")
def _inet_sqlite(type_, compiler, **kw):
    return "TEXT"


@pytest.fixture
def uh(harness):
    tables = [
        User.__table__, RefreshToken.__table__, AdminAuditLog.__table__,
        ArtistApplication.__table__,
    ]

    async def setup():
        async with harness.engine.begin() as conn:
            await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=tables))

    harness.run(setup())
    return harness


def mk_user(h, email, *, admin=False, active=True, name=None, password="pw-12345", **extra):
    async def go():
        async with h.session() as s:
            u = User(
                email=email, full_name=name or email.split("@")[0], is_admin=admin,
                is_active=active, password_hash=get_password_hash(password), **extra,
            )
            s.add(u)
            await s.commit()
            return u

    return h.run(go())


def mk_token(h, user_id, raw="raw-refresh"):
    async def go():
        async with h.session() as s:
            s.add(RefreshToken(
                user_id=user_id, token_hash=hash_token(raw), family_id=uuid.uuid4(),
                expires_at=datetime.now(timezone.utc) + timedelta(days=7),
            ))
            await s.commit()

    h.run(go())


def fetch(h, model, *where):
    async def go():
        async with h.session() as s:
            return (await s.execute(select(model).where(*where))).scalars().all()

    return h.run(go())


def act(h, actor, target_id, action):
    async def go():
        async with h.session() as s:
            return await svc.apply_action(s, actor, target_id, action)

    return h.run(go())


# ---------- service ----------

def test_list_search_filter_paginate(uh):
    mk_user(uh, "alice@x.test", admin=True, name="Alice")
    mk_user(uh, "bob@x.test", name="Bob_100%", artist_status=ArtistStatus.approved)
    for i in range(30):
        mk_user(uh, f"u{i}@y.test")

    async def go(**kw):
        async with uh.session() as s:
            return await svc.list_users(s, **kw)

    rows, total = uh.run(go())
    assert total == 32 and len(rows) == svc.PAGE_SIZE
    rows2, _ = uh.run(go(page=2))
    assert len(rows2) == 7 and not {u.id for u in rows} & {u.id for u in rows2}
    assert [u.email for u in uh.run(go(q="ALICE"))[0]] == ["alice@x.test"]
    assert [u.email for u in uh.run(go(q="100%"))[0]] == ["bob@x.test"]
    assert uh.run(go(q="%"))[1] == 1  # wildcard is escaped, only Bob's literal % matches
    assert [u.email for u in uh.run(go(role="admins"))[0]] == ["alice@x.test"]
    assert [u.email for u in uh.run(go(role="artists"))[0]] == ["bob@x.test"]


def test_detail_counts_artworks_and_sessions(uh):
    u = mk_user(uh, "art@x.test", artist_status=ArtistStatus.approved)
    mk_token(uh, u.id)

    async def seed():
        async with uh.session() as s:
            p = ArtistProfile(user_id=u.id, display_name="Art", slug="art", primary_medium="Oil")
            s.add(p)
            await s.flush()
            for i, deleted in enumerate([None, None, datetime.now(timezone.utc)]):
                s.add(Artwork(
                    artist_id=p.id, title="T", slug=f"s{i}", status=ArtworkStatus.draft,
                    listing_type=ListingType.display, deleted_at=deleted,
                ))
            await s.commit()

    uh.run(seed())

    async def go():
        async with uh.session() as s:
            return await svc.get_user_detail(s, u.id)

    d = uh.run(go())
    assert d.artwork_count == 2 and d.active_sessions == 1
    assert d.user.email == "art@x.test"


def test_deactivate_revokes_tokens_and_audits(uh):
    admin = mk_user(uh, "a@x.test", admin=True)
    target = mk_user(uh, "t@x.test")
    mk_token(uh, target.id)
    act(uh, admin, target.id, svc.ACTION_DEACTIVATE)
    assert fetch(uh, User, User.id == target.id)[0].is_active is False
    tok = fetch(uh, RefreshToken)[0]
    assert tok.revoked_at is not None and tok.revoked_reason == "account_deactivated"
    log = fetch(uh, AdminAuditLog)[0]
    assert (log.actor_id, log.target_user_id, log.action) == (admin.id, target.id, "deactivate")
    act(uh, admin, target.id, svc.ACTION_REACTIVATE)
    assert fetch(uh, User, User.id == target.id)[0].is_active is True
    assert [entry.action for entry in fetch(uh, AdminAuditLog)].count("reactivate") == 1


def test_promote_and_demote(uh):
    admin = mk_user(uh, "a@x.test", admin=True)
    other = mk_user(uh, "o@x.test")
    act(uh, admin, other.id, svc.ACTION_PROMOTE)
    assert fetch(uh, User, User.id == other.id)[0].is_admin
    act(uh, admin, other.id, svc.ACTION_DEMOTE)
    assert not fetch(uh, User, User.id == other.id)[0].is_admin


@pytest.mark.parametrize("action", [svc.ACTION_DEACTIVATE, svc.ACTION_DEMOTE])
def test_cannot_act_on_self(uh, action):
    admin = mk_user(uh, "a@x.test", admin=True)
    mk_user(uh, "b@x.test", admin=True)
    with pytest.raises(svc.UserAdminError, match="own account"):
        act(uh, admin, admin.id, action)
    assert fetch(uh, AdminAuditLog) == []


def test_last_active_admin_protected(uh):
    # The only other admin is deactivated, so the target is the last *active* admin.
    inactive_actor = mk_user(uh, "gone@x.test", admin=True, active=False)
    last = mk_user(uh, "last@x.test", admin=True)
    with pytest.raises(svc.UserAdminError, match="last active admin"):
        act(uh, inactive_actor, last.id, svc.ACTION_DEACTIVATE)
    with pytest.raises(svc.UserAdminError, match="last active admin"):
        act(uh, inactive_actor, last.id, svc.ACTION_DEMOTE)
    row = fetch(uh, User, User.id == last.id)[0]
    assert row.is_admin and row.is_active


def test_redundant_and_invalid_actions(uh):
    admin = mk_user(uh, "a@x.test", admin=True)
    user = mk_user(uh, "u@x.test")
    gone = mk_user(uh, "g@x.test", active=False)
    for target, action in [
        (user, svc.ACTION_REACTIVATE), (gone, svc.ACTION_DEACTIVATE), (admin, svc.ACTION_PROMOTE),
        (user, svc.ACTION_DEMOTE), (gone, svc.ACTION_PROMOTE), (user, "nuke"),
    ]:
        with pytest.raises(svc.UserAdminError):
            act(uh, admin, target.id, action)
    with pytest.raises(svc.UserAdminError, match="not found"):
        act(uh, admin, uuid.uuid4(), svc.ACTION_DEACTIVATE)


# ---------- auth enforcement ----------

@pytest.fixture
def api(uh):
    async def override_db():
        async with uh.session() as s:
            yield s

    app.dependency_overrides[get_db] = override_db
    yield TestClient(app)
    app.dependency_overrides.clear()


def login(api, email, password="pw-12345"):
    return api.post("/api/v1/auth/login", json={"email": email, "password": password})


def test_inactive_user_login_looks_like_bad_credentials(uh, api):
    mk_user(uh, "ok@x.test")
    mk_user(uh, "off@x.test", active=False)
    assert login(api, "ok@x.test").status_code == 200
    bad = login(api, "ok@x.test", "wrong")
    off = login(api, "off@x.test")
    assert off.status_code == bad.status_code == 401
    assert off.json() == bad.json() == {"detail": "Invalid email or password"}


def test_deactivation_kills_access_token_and_refresh(uh, api):
    admin = mk_user(uh, "a@x.test", admin=True)
    mk_user(uh, "u@x.test")
    r = login(api, "u@x.test")
    access = r.json()["access_token"]
    refresh_cookie = r.cookies.get("refresh_token")
    assert api.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {access}"}).status_code == 200

    target = fetch(uh, User, User.email == "u@x.test")[0]
    act(uh, admin, target.id, svc.ACTION_DEACTIVATE)

    me = api.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {access}"})
    assert me.status_code == 401 and me.json() == {"detail": "Not authenticated"}
    ref = api.post("/api/v1/auth/refresh", cookies={"refresh_token": refresh_cookie})
    assert ref.status_code == 401 and ref.json() == {"detail": "Invalid token"}

    act(uh, admin, target.id, svc.ACTION_REACTIVATE)
    assert login(api, "u@x.test").status_code == 200


def test_inactive_admin_cannot_log_into_panel(uh):
    from app.admin_panel.auth import authenticate_admin

    mk_user(uh, "a@x.test", admin=True, active=False)

    async def go():
        async with uh.session() as s:
            return await authenticate_admin(s, "a@x.test", "pw-12345")

    assert uh.run(go()) is None


# ---------- panel routes ----------

class Panel:
    def __init__(self, client, admin):
        self.c, self.admin = client, admin

    def token(self):
        html = self.c.get("/admin/users").text
        return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)

    def post(self, path, token=True):
        data = {"csrf_token": self.token()} if token is True else {}
        return self.c.post(path, data=data, follow_redirects=False)


@pytest.fixture
def panel(uh):
    admin = mk_user(uh, "root@x.test", admin=True, name="Root")
    mk_user(uh, "second@x.test", admin=True)

    async def override_db():
        async with uh.session() as s:
            yield s

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_session_admin_user] = lambda: admin
    yield Panel(TestClient(app), admin)
    app.dependency_overrides.clear()


def test_panel_requires_login():
    app.dependency_overrides.clear()
    c = TestClient(app)
    for path in ("/admin/users", f"/admin/users/{uuid.uuid4()}"):
        r = c.get(path, follow_redirects=False)
        assert r.status_code in (302, 303) and "/admin/login" in r.headers["location"]


def test_list_page_search_nav_and_escaping(uh, panel):
    mk_user(uh, "evil@x.test", name="<script>alert(1)</script>")
    r = panel.c.get("/admin/users", params={"q": "evil"})
    assert r.status_code == 200
    assert "<script>alert(1)</script>" not in r.text and "&lt;script&gt;" in r.text
    assert 'href="/admin/users"' in r.text
    assert "root@x.test" not in r.text  # filtered out
    assert "password_hash" not in r.text and "argon2" not in r.text


def test_detail_hides_secrets(uh, panel):
    u = mk_user(uh, "d@x.test")
    mk_token(uh, u.id, raw="SUPERSECRETTOKEN")
    r = panel.c.get(f"/admin/users/{u.id}")
    assert r.status_code == 200 and "d@x.test" in r.text
    for secret in ("argon2", "SUPERSECRETTOKEN", hash_token("SUPERSECRETTOKEN")):
        assert secret not in r.text


def test_unknown_user_redirects(panel):
    r = panel.c.get(f"/admin/users/{uuid.uuid4()}", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/admin/users?error=")


def test_confirm_then_post_deactivates(uh, panel):
    u = mk_user(uh, "t@x.test")
    page = panel.c.get(f"/admin/users/{u.id}/confirm/deactivate")
    assert page.status_code == 200 and "Yes, deactivate" in page.text
    assert fetch(uh, User, User.id == u.id)[0].is_active  # GET never mutates
    r = panel.post(f"/admin/users/{u.id}/deactivate")
    assert r.status_code == 303 and "notice=" in r.headers["location"]
    assert not fetch(uh, User, User.id == u.id)[0].is_active


def test_post_without_csrf_is_rejected(uh, panel):
    u = mk_user(uh, "t@x.test")
    assert panel.post(f"/admin/users/{u.id}/deactivate", token=False).status_code == 403
    assert fetch(uh, User, User.id == u.id)[0].is_active


def test_self_action_shows_error(uh, panel):
    r = panel.post(f"/admin/users/{panel.admin.id}/demote_admin")
    assert r.status_code == 303 and "error=" in r.headers["location"]
    follow = panel.c.get(r.headers["location"])
    assert "own account" in follow.text
    assert fetch(uh, User, User.id == panel.admin.id)[0].is_admin


def test_unknown_action_rejected(uh, panel):
    u = mk_user(uh, "t@x.test")
    r = panel.post(f"/admin/users/{u.id}/delete")
    assert r.status_code == 303 and "error=" in r.headers["location"]
