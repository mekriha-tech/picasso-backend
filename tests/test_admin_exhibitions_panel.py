import re
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.admin_panel.auth import get_session_admin_user
from app.db.session import get_db
from app.main import app
from app.models.artwork import ArtworkStatus
from app.models.exhibition import Exhibition, ExhibitionArtwork
from app.services.exhibition_admin import PUBLISH_NEEDS_ARTWORKS


class Panel:
    def __init__(self, client, harness):
        self.c, self.h = client, harness

    def token(self):
        html = self.c.get("/admin/exhibitions/new").text
        return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)

    def post(self, path, data=None, token=True):
        data = dict(data or {})
        if token is True:
            data["csrf_token"] = self.token()
        return self.c.post(path, data=data, follow_redirects=False)

    def create(self, **over):
        data = {"title": "Spring Show", "viewer_kind": "first_party"}
        data.update(over)
        r = self.post("/admin/exhibitions/new", data)
        assert r.status_code == 303, r.text
        return uuid.UUID(re.search(r"/exhibitions/([0-9a-f-]{36})/edit", r.headers["location"]).group(1))

    def row(self, ex_id):
        async def go():
            async with self.h.session() as s:
                return await s.get(Exhibition, ex_id)

        return self.h.run(go())

    def assigned(self, ex_id):
        async def go():
            async with self.h.session() as s:
                rows = await s.execute(
                    select(ExhibitionArtwork).where(ExhibitionArtwork.exhibition_id == ex_id)
                )
                return {r.artwork_id: r.wall_position for r in rows.scalars()}

        return self.h.run(go())


@pytest.fixture
def panel(harness):
    async def override_db():
        async with harness.session() as s:
            yield s

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_session_admin_user] = lambda: object()
    yield Panel(TestClient(app), harness)
    app.dependency_overrides.clear()


def test_pages_require_login():
    app.dependency_overrides.clear()
    c = TestClient(app)
    for path in (
        "/admin/exhibitions", "/admin/exhibitions/new",
        f"/admin/exhibitions/{uuid.uuid4()}/edit", f"/admin/exhibitions/{uuid.uuid4()}/delete",
    ):
        r = c.get(path, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/admin/login", path


def test_create_vendor_gallery_requires_url_and_shows_field_error(panel):
    html = panel.c.get("/admin/exhibitions/new").text
    assert 'name="viewer_kind"' in html and 'name="csrf_token"' in html
    r = panel.post("/admin/exhibitions/new", {"title": "Tour", "viewer_kind": "vendor_embed"})
    assert r.status_code == 422
    assert "Enter a full web address starting with https://." in r.text
    r = panel.post("/admin/exhibitions/new", {"title": "Tour"})  # viewer_kind is not defaulted
    assert r.status_code == 422 and "Choose how visitors will view this gallery." in r.text
    ok = panel.create(title="Tour", viewer_kind="vendor_embed", vr_tour_url="https://my.artsteps.com/x")
    assert panel.row(ok).is_published is False


def test_create_first_party_then_assign_artworks_then_publish(panel):
    ex_id = panel.create()
    art1 = panel.h.make_artwork(panel.h.make_artist("Elena Frost"), title="Dawn", status=ArtworkStatus.published)
    art2 = panel.h.make_artwork(panel.h.make_artist("Other"), title="Dusk", status=ArtworkStatus.draft)
    page = panel.c.get(f"/admin/exhibitions/{ex_id}/edit").text
    assert "Dawn" in page and "Dusk" in page and "Elena Frost" in page
    assert "published" in page and "draft" in page  # status shown next to each artwork

    r = panel.post(
        f"/admin/exhibitions/{ex_id}/artworks",
        {"artwork": [str(art1), str(art2)], f"position_{art1}": "1", f"position_{art2}": ""},
    )
    assert r.status_code == 303 and "notice=" in r.headers["location"]
    assert panel.assigned(ex_id) == {art1: 1, art2: None}

    r = panel.post(f"/admin/exhibitions/{ex_id}/artworks", {"artwork": [str(art1), str(art2)],
                   f"position_{art1}": "2", f"position_{art2}": "2"})
    assert "error=" in r.headers["location"]  # duplicate wall positions

    r = panel.post(f"/admin/exhibitions/{ex_id}/publish")
    assert r.status_code == 303 and "notice=" in r.headers["location"]
    assert panel.row(ex_id).is_published is True
    panel.post(f"/admin/exhibitions/{ex_id}/unpublish")
    assert panel.row(ex_id).is_published is False


def test_publish_empty_first_party_shows_service_message(panel):
    from urllib.parse import quote

    ex_id = panel.create()
    r = panel.post(f"/admin/exhibitions/{ex_id}/publish?next=edit")
    assert r.status_code == 303
    assert f"error={quote(PUBLISH_NEEDS_ARTWORKS)}" in r.headers["location"]
    assert panel.row(ex_id).is_published is False


def test_post_without_csrf_token_is_rejected(panel):
    panel.token()
    assert panel.post("/admin/exhibitions/new", {"title": "x", "viewer_kind": "first_party"}, token=False).status_code == 403
    ex_id = panel.create()
    assert panel.post(f"/admin/exhibitions/{ex_id}/publish", token=False).status_code == 403
    assert panel.post(f"/admin/exhibitions/{ex_id}/delete", token=False).status_code == 403
    assert panel.row(ex_id) is not None


def test_delete_requires_confirm_page_then_post(panel):
    ex_id = panel.create()
    page = panel.c.get(f"/admin/exhibitions/{ex_id}/delete")
    assert page.status_code == 200 and "Spring Show" in page.text
    assert panel.row(ex_id) is not None  # GET did not delete
    r = panel.post(f"/admin/exhibitions/{ex_id}/delete")
    assert r.status_code == 303 and r.headers["location"].startswith("/admin/exhibitions")
    assert panel.row(ex_id) is None


def test_huge_wall_position_redirects_with_error(panel):
    ex_id = panel.create()
    art = panel.h.make_artwork(panel.h.make_artist())
    r = panel.post(
        f"/admin/exhibitions/{ex_id}/artworks",
        {"artwork": [str(art)], f"position_{art}": "9" * 5000},
    )
    assert r.status_code == 303 and "error=" in r.headers["location"]
    assert panel.assigned(ex_id) == {}


def test_publish_with_only_draft_artworks_shows_service_message(panel):
    from urllib.parse import quote

    ex_id = panel.create()
    art = panel.h.make_artwork(panel.h.make_artist(), status=ArtworkStatus.draft)
    panel.post(f"/admin/exhibitions/{ex_id}/artworks", {"artwork": [str(art)], f"position_{art}": "1"})
    r = panel.post(f"/admin/exhibitions/{ex_id}/publish?next=edit")
    assert f"error={quote(PUBLISH_NEEDS_ARTWORKS)}" in r.headers["location"]
    assert panel.row(ex_id).is_published is False


EMPTY_WARNING = "Published but no visible artworks"


def _draft_out(panel, art):
    async def go():
        async with panel.h.session() as s:
            from app.models.artwork import Artwork

            (await s.get(Artwork, art)).status = ArtworkStatus.draft
            await s.commit()

    panel.h.run(go())


def _published_gallery(panel, title, artist):
    ex_id = panel.create(title=title)
    art = panel.h.make_artwork(artist, status=ArtworkStatus.published)
    panel.post(f"/admin/exhibitions/{ex_id}/artworks", {"artwork": [str(art)], f"position_{art}": "1"})
    panel.post(f"/admin/exhibitions/{ex_id}/publish")
    assert panel.row(ex_id).is_published is True
    return ex_id, art


def test_list_warns_for_published_first_party_with_no_visible_artworks(panel):
    ex_id, art = _published_gallery(panel, "Emptied", panel.h.make_artist())
    _draft_out(panel, art)
    assert EMPTY_WARNING in panel.c.get("/admin/exhibitions").text
    assert EMPTY_WARNING in panel.c.get(f"/admin/exhibitions/{ex_id}/edit").text


def test_list_no_warning_for_healthy_or_unpublished_gallery(panel):
    artist = panel.h.make_artist()
    _published_gallery(panel, "Healthy", artist)
    unpublished = panel.create(title="Unpublished")
    art = panel.h.make_artwork(artist, status=ArtworkStatus.draft)
    panel.post(f"/admin/exhibitions/{unpublished}/artworks", {"artwork": [str(art)]})
    assert EMPTY_WARNING not in panel.c.get("/admin/exhibitions").text
    assert EMPTY_WARNING not in panel.c.get(f"/admin/exhibitions/{unpublished}/edit").text
