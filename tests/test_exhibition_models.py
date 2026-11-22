import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.exhibition import Exhibition, ExhibitionArtwork, ViewerKind


def _ex(slug="show-1", viewer_kind="first_party", vr_tour_url=None):
    return Exhibition(title="Show", slug=slug, viewer_kind=viewer_kind, vr_tour_url=vr_tour_url)


def _commit(harness, *objs):
    async def go():
        async with harness.session() as s:
            s.add_all(objs)
            await s.commit()

    harness.run(go())


def test_viewer_kind_values():
    assert {k.value for k in ViewerKind} == {"vendor_embed", "first_party"}


def test_vendor_embed_requires_vr_tour_url(harness):
    with pytest.raises(IntegrityError):
        _commit(harness, _ex(viewer_kind="vendor_embed", vr_tour_url=None))


def test_vendor_embed_with_url_ok(harness):
    _commit(harness, _ex(viewer_kind="vendor_embed", vr_tour_url="https://example.com/tour"))


def test_first_party_does_not_need_vr_tour_url(harness):
    _commit(harness, _ex(viewer_kind="first_party", vr_tour_url=None))


def test_unknown_viewer_kind_rejected(harness):
    with pytest.raises(IntegrityError):
        _commit(harness, _ex(viewer_kind="hologram"))


def test_slug_unique(harness):
    with pytest.raises(IntegrityError):
        _commit(harness, _ex(slug="dup"), _ex(slug="dup"))


def test_artwork_assigned_once_per_exhibition(harness):
    ex = _ex()
    ex.id = uuid.uuid4()
    _commit(harness, ex)
    aid = uuid.uuid4()
    with pytest.raises(IntegrityError):
        _commit(
            harness,
            ExhibitionArtwork(exhibition_id=ex.id, artwork_id=aid),
            ExhibitionArtwork(exhibition_id=ex.id, artwork_id=aid),
        )
