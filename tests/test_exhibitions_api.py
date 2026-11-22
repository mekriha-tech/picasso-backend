import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_optional_user
from app.db.session import get_db
from app.main import app
from app.models.artwork import Artwork, ArtworkStatus, ListingType
from app.models.artwork_image import ArtworkImage
from app.models.exhibition import Exhibition, ExhibitionArtwork
from app.services.teaser import TEASER_LIMIT

LIST_KEYS = {"id", "title", "slug", "cover_image_url", "viewer_kind"}


@pytest.fixture
def api(harness):
    async def override_db():
        async with harness.session() as s:
            yield s

    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    client.h = harness
    client.artist = harness.make_artist()
    yield client
    app.dependency_overrides.clear()


def _add(h, *objs):
    async def go():
        async with h.session() as s:
            s.add_all(objs)
            await s.commit()

    h.run(go())


def exhibition(api, slug="show", published=True, kind="vendor_embed", sort_order=0, **kw):
    ex = Exhibition(
        id=uuid.uuid4(), title=kw.pop("title", slug.title()), slug=slug, viewer_kind=kind,
        vr_tour_url="https://x.example/tour" if kind == "vendor_embed" else None,
        is_published=published, sort_order=sort_order, **kw,
    )
    _add(api.h, ex)
    return ex.id


def artwork(api, ex_id=None, wall=None, status=ArtworkStatus.published, slug=None, **kw):
    kw.setdefault("listing_type", ListingType.display)
    a = Artwork(
        id=uuid.uuid4(), artist_id=api.artist, title=kw.pop("title", "Work"),
        slug=slug or f"w-{uuid.uuid4().hex[:8]}", status=status, **kw,
    )
    _add(api.h, a)
    if ex_id is not None:
        _add(api.h, ExhibitionArtwork(exhibition_id=ex_id, artwork_id=a.id, wall_position=wall))
    return a.id


def test_list_only_published_ordered_by_sort_order(api):
    exhibition(api, "b", sort_order=2)
    exhibition(api, "a", sort_order=1)
    exhibition(api, "hidden", published=False)
    r = api.get("/api/v1/exhibitions")
    assert r.status_code == 200
    items = r.json()["items"]
    assert [i["slug"] for i in items] == ["a", "b"]
    assert set(items[0]) == LIST_KEYS


@pytest.mark.parametrize("slug", ["nope", "draft"])
def test_unknown_and_unpublished_slug_404(api, slug):
    exhibition(api, "draft", published=False)
    r = api.get(f"/api/v1/exhibitions/{slug}")
    assert r.status_code == 404
    assert r.json() == {"detail": "Exhibition not found"}


def test_vendor_detail_has_url_and_no_artworks(api):
    exhibition(api, "v", kind="vendor_embed", description="d")
    body = api.get("/api/v1/exhibitions/v").json()
    assert body["vr_tour_url"] == "https://x.example/tour"
    assert "artworks" not in body
    assert body["description"] == "d"
    assert "starts_on" in body and "ends_on" in body


def test_first_party_detail_has_artworks_and_no_url(api):
    ex = exhibition(api, "f", kind="first_party")
    artwork(api, ex, wall=1)
    body = api.get("/api/v1/exhibitions/f").json()
    assert "vr_tour_url" not in body
    assert len(body["artworks"]) == 1
    art = body["artworks"][0]
    for k in ("wall_position", "width_cm", "height_cm", "texture_url", "image_width_px",
              "image_height_px", "primary_image_url", "artist"):
        assert k in art


def test_artworks_ordered_by_wall_position_nulls_last(api):
    ex = exhibition(api, "f", kind="first_party")
    artwork(api, ex, wall=None, title="none")
    artwork(api, ex, wall=2, title="two")
    artwork(api, ex, wall=1, title="one")
    titles = [a["title"] for a in api.get("/api/v1/exhibitions/f").json()["artworks"]]
    assert titles == ["one", "two", "none"]


def test_non_public_artworks_excluded(api):
    ex = exhibition(api, "f", kind="first_party")
    artwork(api, ex, 1, title="ok")
    artwork(api, ex, 2, status=ArtworkStatus.draft, title="draft")
    artwork(api, ex, 3, status=ArtworkStatus.removed, title="removed")
    titles = [a["title"] for a in api.get("/api/v1/exhibitions/f").json()["artworks"]]
    assert titles == ["ok"]


def test_price_is_string_and_sold_status_present(api):
    ex = exhibition(api, "f", kind="first_party")
    artwork(api, ex, 1, listing_type=ListingType.sale, price=Decimal("1200.00"),
            status=ArtworkStatus.sold, sold=True, sold_price=Decimal("1100.00"))
    art = api.get("/api/v1/exhibitions/f").json()["artworks"][0]
    assert art["price"] == "1200.00"
    assert art["sold_price"] == "1100.00"
    assert art["status"] == "sold"
    assert art["sold"] is True


def test_anonymous_gets_teaser_subset_and_flag(api):
    ex = exhibition(api, "f", kind="first_party")
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    ids = []
    for i in range(TEASER_LIMIT + 4):
        ids.append(str(artwork(api, ex, i, published_at=base + timedelta(days=i))))
    anon = api.get("/api/v1/exhibitions/f").json()
    assert anon["teaser"] is True
    got = {a["id"] for a in anon["artworks"]}
    assert got < set(ids)
    assert len(got) == TEASER_LIMIT
    assert got == set(ids[-TEASER_LIMIT:])

    app.dependency_overrides[get_optional_user] = lambda: object()
    full = api.get("/api/v1/exhibitions/f").json()
    assert full["teaser"] is False
    assert {a["id"] for a in full["artworks"]} == set(ids)


def test_texture_url_prefers_preview(api):
    ex = exhibition(api, "f", kind="first_party")
    a1 = artwork(api, ex, 1, primary_image_url="https://c/legacy1.jpg")
    a2 = artwork(api, ex, 2, primary_image_url="https://c/legacy2.jpg")
    a3 = artwork(api, ex, 3, primary_image_url="https://c/legacy3.jpg")
    _add(api.h,
         ArtworkImage(artwork_id=a1, url="https://c/full1.jpg", preview_url="https://c/prev1.jpg",
                      is_primary=True, width_px=800, height_px=600),
         ArtworkImage(artwork_id=a2, url="https://c/full2.jpg", is_primary=True))
    arts = {a["id"]: a for a in api.get("/api/v1/exhibitions/f").json()["artworks"]}
    assert arts[str(a1)]["texture_url"] == "https://c/prev1.jpg"
    assert arts[str(a1)]["image_width_px"] == 800
    assert arts[str(a1)]["image_height_px"] == 600
    assert arts[str(a2)]["texture_url"] == "https://c/full2.jpg"
    assert arts[str(a3)]["texture_url"] == "https://c/legacy3.jpg"
    assert arts[str(a3)]["image_width_px"] is None
