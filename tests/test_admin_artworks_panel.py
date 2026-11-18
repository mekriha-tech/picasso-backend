import re
import uuid
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.admin_panel.auth import get_session_admin_user
from app.db.session import get_db
from app.main import app
from app.models.artwork import Artwork, ArtworkStatus
from app.services.storage import UnconfiguredStorage, get_image_storage
from tests.conftest import make_image_bytes
from tests.fakes import InMemoryStorage


class Panel:
    def __init__(self, client, harness, storage):
        self.c, self.h, self.storage = client, harness, storage
        self.artist_id = harness.make_artist("Elena Frost")

    def token(self):
        html = self.c.get("/admin/artworks/new").text
        return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)

    def post(self, path, data=None, files=None, token=True, **kw):
        data = dict(data or {})
        if token is True:
            data["csrf_token"] = self.token()
        return self.c.post(path, data=data, files=files, follow_redirects=False, **kw)

    def form(self, **over):
        values = {
            "artist_id": str(self.artist_id), "title": "Sunset", "year": "2021", "medium": "Oil",
            "listing_type": "display", "price": "",
        }
        values.update(over)
        return values

    def create(self, **over):
        r = self.post("/admin/artworks/new", self.form(**over))
        assert r.status_code == 303, r.text
        return uuid.UUID(re.search(r"/artworks/([0-9a-f-]{36})/edit", r.headers["location"]).group(1))

    def artwork(self, aid):
        async def go():
            async with self.h.session() as s:
                return (await s.execute(select(Artwork).where(Artwork.id == aid))).scalars().one()

        return self.h.run(go())

    def upload(self, aid, n=1, data=None):
        files = [("files", (f"p{i}.jpg", data or make_image_bytes(), "image/jpeg")) for i in range(n)]
        return self.post(f"/admin/artworks/{aid}/images", files=files)


@pytest.fixture
def panel(harness):
    storage = InMemoryStorage()

    async def override_db():
        async with harness.session() as s:
            yield s

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_session_admin_user] = lambda: object()
    app.dependency_overrides[get_image_storage] = lambda: storage
    yield Panel(TestClient(app), harness, storage)
    app.dependency_overrides.clear()


def location(r):
    return r.headers["location"]


def test_admin_pages_require_login():
    app.dependency_overrides.clear()
    c = TestClient(app)
    for path in ("/admin/artworks/new", f"/admin/artworks/{uuid.uuid4()}/edit"):
        r = c.get(path, follow_redirects=False)
        assert r.status_code == 303 and location(r) == "/admin/login"


def test_new_form_renders_with_csrf_and_artists(panel):
    html = panel.c.get("/admin/artworks/new").text
    assert 'name="csrf_token"' in html and "Elena Frost" in html
    assert 'value="auction"' not in html  # auctions cannot be created here yet


def test_post_without_or_with_wrong_csrf_is_403_and_changes_nothing(panel):
    panel.token()
    assert panel.post("/admin/artworks/new", panel.form(), token=False).status_code == 403
    r = panel.post("/admin/artworks/new", {**panel.form(), "csrf_token": "forged"}, token=False)
    assert r.status_code == 403
    assert panel.post("/admin/logout", token=False).status_code == 403
    assert panel.h.run(_count_impl(panel.h)) == 0


async def _count_impl(h):
    async with h.session() as s:
        return len((await s.execute(select(Artwork.id))).all())


def test_create_validation_errors_use_fixed_copy_and_keep_input(panel):
    r = panel.post("/admin/artworks/new", panel.form(title="", listing_type="sale", price=""))
    assert r.status_code == 422
    assert "Give the artwork a title." in r.text
    assert "A price is required for items listed for sale." in r.text
    r = panel.post("/admin/artworks/new", panel.form(listing_type="display", price="10"))
    assert "Items on display only can&#39;t have a price." in r.text
    r = panel.post("/admin/artworks/new", panel.form(artist_id=str(uuid.uuid4()), year="abc"))
    assert "Choose an artist." in r.text and "Year must be between" in r.text


def test_create_edit_flow_and_catalogue_slug(panel):
    aid = panel.create(title="Blue Horizon", listing_type="sale", price="12500.50")
    art = panel.artwork(aid)
    assert art.slug == "blue-horizon" and art.status == ArtworkStatus.draft
    assert Decimal(art.price) == Decimal("12500.50")
    assert panel.artwork(panel.create(title="Blue Horizon")).slug == "blue-horizon-2"

    r = panel.post(f"/admin/artworks/{aid}/edit", panel.form(title="Blue Horizon II", listing_type="display"))
    assert r.status_code == 303
    art = panel.artwork(aid)
    assert art.title == "Blue Horizon II" and art.price is None and art.slug == "blue-horizon"


def test_all_output_is_escaped(panel):
    aid = panel.create(title='<script>alert("x")</script>')
    page = panel.c.get(f"/admin/artworks/{aid}/edit").text
    assert "<script>alert" not in page and "&lt;script&gt;" in page
    assert "<script>alert" not in panel.c.get("/admin/artworks").text
    r = panel.c.get(f"/admin/artworks/{aid}/edit?error=%3Cimg%20src%3Dx%3E&notice=%3Cb%3E")
    assert "<img src=x>" not in r.text and "<b>" not in r.text


def test_upload_many_thumbnails_reorder_primary_delete(panel):
    aid = panel.create()
    r = panel.upload(aid, n=3)
    assert r.status_code == 303 and "Uploaded%203%20images." in location(r)
    page = panel.c.get(f"/admin/artworks/{aid}/edit").text
    assert page.count('<div class="card">') == 3
    assert page.count("cdn.example.test/staging/artworks/") == 3  # preview thumbnails

    def ids():
        return re.findall(r"/images/([0-9a-f-]{36})/delete", panel.c.get(f"/admin/artworks/{aid}/edit").text)

    first, second, third = ids()
    panel.post(f"/admin/artworks/{aid}/images/{third}/move", {"direction": "up"})
    assert ids() == [first, third, second]
    panel.post(f"/admin/artworks/{aid}/images/{second}/primary")
    assert panel.artwork(aid).primary_image_url is not None
    r = panel.post(f"/admin/artworks/{aid}/images/{first}/delete")
    assert "Image%20deleted." in location(r) and len(ids()) == 2
    assert len(panel.storage.deleted) == 2


def test_upload_rejects_non_images_but_keeps_good_ones(panel):
    aid = panel.create()
    files = [
        ("files", ("good.jpg", make_image_bytes(), "image/jpeg")),
        ("files", ("<b>bad</b>.jpg", b"not an image", "image/jpeg")),
    ]
    r = panel.post(f"/admin/artworks/{aid}/images", files=files)
    loc = location(r)
    assert "Uploaded%201%20image." in loc
    assert "not%20a%20valid%20JPEG" in loc
    page = panel.c.get(loc).text
    assert "<b>bad</b>" not in page and "&lt;b&gt;bad&lt;/b&gt;" in page
    assert len(panel.storage.objects) == 2


def test_upload_disabled_when_storage_not_configured(panel):
    aid = panel.create()
    app.dependency_overrides[get_image_storage] = lambda: UnconfiguredStorage()
    assert "not configured" in panel.c.get(f"/admin/artworks/{aid}/edit").text
    r = panel.upload(aid)
    assert r.status_code == 303 and "not%20configured" in location(r)


def test_publish_rule_10_copy_and_last_image_protection(panel):
    aid = panel.create()
    r = panel.post(f"/admin/artworks/{aid}/status?next=edit", {"new_status": "published"})
    assert "at%20least%20one%20image" in location(r)
    assert panel.artwork(aid).status == ArtworkStatus.draft

    panel.upload(aid)
    r = panel.post(f"/admin/artworks/{aid}/status?next=edit", {"new_status": "published"})
    assert "Status%20updated" in location(r)
    assert panel.artwork(aid).status == ArtworkStatus.published

    image_id = re.search(
        r"/images/([0-9a-f-]{36})/delete", panel.c.get(f"/admin/artworks/{aid}/edit").text
    ).group(1)
    r = panel.post(f"/admin/artworks/{aid}/images/{image_id}/delete")
    assert "at%20least%20one%20image" in location(r)
    assert panel.storage.deleted == []


def test_edit_cannot_strip_publish_fields_from_published_artwork(panel):
    aid = panel.create()
    panel.upload(aid)
    panel.post(f"/admin/artworks/{aid}/status", {"new_status": "published"})
    r = panel.post(f"/admin/artworks/{aid}/edit", panel.form(year="", medium=""))
    assert r.status_code == 422
    assert "Artwork needs a title, year, and medium before it can be published." in r.text


def test_delete_artwork_confirmation_and_soft_delete(panel):
    aid = panel.create(title="To Remove")
    page = panel.c.get(f"/admin/artworks/{aid}/delete")
    assert page.status_code == 200 and "To Remove" in page.text and "Yes, delete it" in page.text
    assert panel.artwork(aid).deleted_at is None  # GET changes nothing
    r = panel.post(f"/admin/artworks/{aid}/delete")
    assert r.status_code == 303 and location(r).startswith("/admin/artworks?notice=")
    art = panel.artwork(aid)
    assert art.deleted_at is not None and art.status == ArtworkStatus.removed
    assert "To Remove" not in panel.c.get("/admin/artworks").text
    assert location(panel.c.get(f"/admin/artworks/{aid}/edit", follow_redirects=False)).startswith(
        "/admin/artworks?error="
    )


@pytest.mark.parametrize("status", [ArtworkStatus.sold, ArtworkStatus.reserved])
def test_sold_and_reserved_artworks_cannot_be_deleted(panel, status):
    aid = panel.h.make_artwork(panel.artist_id, status=status)
    r = panel.post(f"/admin/artworks/{aid}/delete")
    assert "/delete?error=" in location(r)
    assert panel.artwork(aid).deleted_at is None


def test_sold_artwork_price_and_listing_type_locked(panel, harness):
    aid = harness.make_artwork(panel.artist_id, status=ArtworkStatus.sold)
    r = panel.post(
        f"/admin/artworks/{aid}/edit", panel.form(listing_type="sale", price="5")
    )
    assert r.status_code == 422 and "Sold artworks can&#39;t change listing type or price." in r.text
    assert panel.post(f"/admin/artworks/{aid}/edit", panel.form(title="Renamed")).status_code == 303
