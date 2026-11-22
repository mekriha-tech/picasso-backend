import uuid

import pytest
from sqlalchemy import select

from app.models.artwork import Artwork
from app.models.exhibition import Exhibition, ExhibitionArtwork
from app.services import exhibition_admin as svc
from app.services.exhibition_admin import ExhibitionValidationError, PUBLISH_NEEDS_ARTWORKS


def _form(**over):
    raw = {"title": "Spring Show", "viewer_kind": "vendor_embed",
           "vr_tour_url": "https://my.artsteps.com/x", "sort_order": "0"}
    raw.update(over)
    return raw


def _call(h, fn, *args):
    async def go():
        async with h.session() as s:
            return await fn(s, *args)

    return h.run(go())


def _create(h, **over):
    return _call(h, svc.create_exhibition, svc.clean_exhibition_form(_form(**over)))


def _get(h, ex_id):
    async def go():
        async with h.session() as s:
            return await s.get(Exhibition, ex_id)

    return h.run(go())


def _assignments(h, ex_id):
    async def go():
        async with h.session() as s:
            rows = await s.execute(
                select(ExhibitionArtwork).where(ExhibitionArtwork.exhibition_id == ex_id)
            )
            return rows.scalars().all()

    return h.run(go())


@pytest.mark.parametrize("url", ["javascript:alert(1)", "data:text/html,x", "ftp://x"])
def test_vendor_url_scheme_rejected(url):
    with pytest.raises(ExhibitionValidationError) as e:
        svc.clean_exhibition_form(_form(vr_tour_url=url))
    assert e.value.errors["vr_tour_url"] == "Enter a full web address starting with https://."


def test_vendor_url_https_passes():
    assert svc.clean_exhibition_form(_form())["vr_tour_url"] == "https://my.artsteps.com/x"


def test_vendor_embed_requires_url():
    with pytest.raises(ExhibitionValidationError) as e:
        svc.clean_exhibition_form(_form(vr_tour_url="  "))
    assert "vr_tour_url" in e.value.errors


def test_first_party_ignores_blank_url():
    clean = svc.clean_exhibition_form(_form(viewer_kind="first_party", vr_tour_url=""))
    assert clean["viewer_kind"] == "first_party" and clean["vr_tour_url"] is None


@pytest.mark.parametrize("kind", [None, "", "bogus"])
def test_viewer_kind_must_be_explicit(kind):
    raw = _form()
    if kind is None:
        del raw["viewer_kind"]
    else:
        raw["viewer_kind"] = kind
    with pytest.raises(ExhibitionValidationError) as e:
        svc.clean_exhibition_form(raw)
    assert "viewer_kind" in e.value.errors


def test_title_sort_order_and_dates():
    with pytest.raises(ExhibitionValidationError) as e:
        svc.clean_exhibition_form(
            _form(title="", sort_order="40000", starts_on="nope", ends_on="2026-13-01")
        )
    assert set(e.value.errors) == {"title", "sort_order", "starts_on", "ends_on"}
    with pytest.raises(ExhibitionValidationError) as e:
        svc.clean_exhibition_form(_form(title="x" * 201))
    assert "title" in e.value.errors
    clean = svc.clean_exhibition_form(_form(starts_on="2026-01-02", sort_order="5"))
    assert str(clean["starts_on"]) == "2026-01-02" and clean["sort_order"] == 5


def test_create_generates_unique_slug_and_unpublished(harness):
    a, b = _create(harness), _create(harness)
    assert (a.slug, b.slug) == ("spring-show", "spring-show-2")
    assert a.is_published is False


def test_publish_first_party_without_artworks_refused(harness):
    ex = _create(harness, viewer_kind="first_party", vr_tour_url="")
    with pytest.raises(ExhibitionValidationError) as e:
        _call(harness, svc.set_published, ex.id, True)
    assert PUBLISH_NEEDS_ARTWORKS in e.value.errors.values()
    assert _get(harness, ex.id).is_published is False


def test_publish_vendor_without_artworks_allowed(harness):
    ex = _create(harness)
    assert _call(harness, svc.set_published, ex.id, True).is_published is True


def test_publish_first_party_with_artworks_allowed(harness):
    ex = _create(harness, viewer_kind="first_party", vr_tour_url="")
    art = harness.make_artwork(harness.make_artist())
    _call(harness, svc.set_artworks, ex.id, {art: None})
    assert _call(harness, svc.set_published, ex.id, True).is_published is True


def test_unpublish_always_allowed(harness):
    ex = _create(harness, viewer_kind="first_party", vr_tour_url="")
    art = harness.make_artwork(harness.make_artist())
    _call(harness, svc.set_artworks, ex.id, {art: None})
    _call(harness, svc.set_published, ex.id, True)
    assert _call(harness, svc.set_published, ex.id, False).is_published is False


def test_update_published_vendor_to_first_party_without_artworks_refused(harness):
    ex = _create(harness)
    _call(harness, svc.set_published, ex.id, True)
    clean = svc.clean_exhibition_form(_form(viewer_kind="first_party", vr_tour_url=""))
    with pytest.raises(ExhibitionValidationError) as e:
        _call(harness, svc.update_exhibition, ex.id, clean)
    assert PUBLISH_NEEDS_ARTWORKS in e.value.errors.values()
    row = _get(harness, ex.id)
    assert row.viewer_kind == "vendor_embed" and row.is_published is True


def test_update_keeps_slug(harness):
    ex = _create(harness)
    clean = svc.clean_exhibition_form(_form(title="Renamed"))
    out = _call(harness, svc.update_exhibition, ex.id, clean)
    assert out.title == "Renamed" and out.slug == "spring-show"


def test_set_artworks_replaces_assignment_and_rejects_duplicate_positions(harness):
    ex = _create(harness, viewer_kind="first_party", vr_tour_url="")
    artist = harness.make_artist()
    a1, a2, a3 = (harness.make_artwork(artist) for _ in range(3))
    _call(harness, svc.set_artworks, ex.id, {a1: 1, a2: 2})
    with pytest.raises(ExhibitionValidationError) as e:
        _call(harness, svc.set_artworks, ex.id, {a2: 3, a3: 3})
    assert "artworks" in e.value.errors
    assert {r.artwork_id for r in _assignments(harness, ex.id)} == {a1, a2}  # unchanged
    _call(harness, svc.set_artworks, ex.id, {a2: None, a3: None})  # nulls may repeat
    assert {r.artwork_id for r in _assignments(harness, ex.id)} == {a2, a3}


def test_set_artworks_unknown_artwork_rejected(harness):
    ex = _create(harness)
    with pytest.raises(ExhibitionValidationError) as e:
        _call(harness, svc.set_artworks, ex.id, {uuid.uuid4(): 1})
    assert "artworks" in e.value.errors


def test_set_artworks_cannot_empty_published_first_party(harness):
    ex = _create(harness, viewer_kind="first_party", vr_tour_url="")
    art = harness.make_artwork(harness.make_artist())
    _call(harness, svc.set_artworks, ex.id, {art: None})
    _call(harness, svc.set_published, ex.id, True)
    with pytest.raises(ExhibitionValidationError):
        _call(harness, svc.set_artworks, ex.id, {})
    assert len(_assignments(harness, ex.id)) == 1


def test_not_found(harness):
    with pytest.raises(svc.ExhibitionNotFoundError):
        _call(harness, svc.set_published, uuid.uuid4(), True)


def test_delete_removes_assignments(harness):
    ex = _create(harness)
    art = harness.make_artwork(harness.make_artist())
    _call(harness, svc.set_artworks, ex.id, {art: 1})
    _call(harness, svc.delete_exhibition, ex.id)
    assert _get(harness, ex.id) is None
    assert _assignments(harness, ex.id) == []

    async def fetch():
        async with harness.session() as s:
            return (await s.execute(select(Artwork).where(Artwork.id == art))).scalar_one_or_none()

    assert harness.run(fetch()) is not None
