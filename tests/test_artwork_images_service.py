import io
import uuid

import pytest
from PIL import Image
from sqlalchemy import select

from app.models.artwork import Artwork, ArtworkStatus
from app.models.artwork_image import ArtworkImage
from app.services import artwork_images as svc
from app.services import image_processing as ip
from app.services.storage import StorageError, StorageNotConfiguredError, UnconfiguredStorage
from tests.conftest import make_image_bytes
from tests.fakes import InMemoryStorage


def setup_artwork(h, status=ArtworkStatus.draft):
    return h.make_artwork(h.make_artist(f"Artist {uuid.uuid4().hex[:6]}"), status=status)


def upload(h, storage, artwork_id, **kw):
    async def go():
        async with h.session() as s:
            return await svc.add_image(s, storage, artwork_id, make_image_bytes(**kw))

    return h.run(go())


def images(h, artwork_id):
    async def go():
        async with h.session() as s:
            return await svc.list_images(s, artwork_id)

    return h.run(go())


def artwork(h, artwork_id):
    async def go():
        async with h.session() as s:
            return (await s.execute(select(Artwork).where(Artwork.id == artwork_id))).scalars().one()

    return h.run(go())


def delete(h, st, aid, image_id):
    async def go():
        async with h.session() as s:
            await svc.delete_image(s, st, aid, image_id)

    return h.run(go())


def test_first_upload_is_primary_and_sets_artwork_url(harness):
    st, aid = InMemoryStorage(), setup_artwork(harness)
    img = upload(harness, st, aid)
    assert img.is_primary and img.sort_order == 0
    assert artwork(harness, aid).primary_image_url == img.url
    assert img.width_px == 300 and img.height_px == 200
    assert img.preview_url and img.preview_url != img.url


def test_keys_are_prefixed_random_and_separate(harness):
    st, aid = InMemoryStorage(key_prefix="staging"), setup_artwork(harness)
    a, b = upload(harness, st, aid), upload(harness, st, aid)
    keys = [a.storage_key, a.preview_storage_key, b.storage_key, b.preview_storage_key]
    assert len(set(keys)) == 4 and set(keys) == set(st.objects)
    for k in keys:
        assert k.startswith("staging/artworks/") and k.endswith(".webp")
    assert not b.is_primary and b.sort_order == 1


def test_stored_objects_have_no_exif(harness):
    st, aid = InMemoryStorage(), setup_artwork(harness)
    upload(harness, st, aid, exif=True)
    for data, ctype in st.objects.values():
        assert ctype == "image/webp"
        assert not Image.open(io.BytesIO(data)).getexif()


def test_invalid_upload_stores_nothing(harness):
    st, aid = InMemoryStorage(), setup_artwork(harness)

    async def go():
        async with harness.session() as s:
            await svc.add_image(s, st, aid, b"nope")

    with pytest.raises(ip.InvalidImageError):
        harness.run(go())
    assert not st.objects and images(harness, aid) == []


def test_unconfigured_storage_is_rejected(harness):
    aid = setup_artwork(harness)

    async def go():
        async with harness.session() as s:
            await svc.add_image(s, UnconfiguredStorage(), aid, make_image_bytes())

    with pytest.raises(StorageNotConfiguredError):
        harness.run(go())


def test_storage_failure_cleans_up_and_leaves_no_row(harness):
    st, aid = InMemoryStorage(fail_put_after=1), setup_artwork(harness)
    with pytest.raises(StorageError):
        upload(harness, st, aid)
    assert not st.objects and images(harness, aid) == []


def test_image_limit(harness, monkeypatch):
    monkeypatch.setattr(svc, "MAX_IMAGES_PER_ARTWORK", 1)
    st, aid = InMemoryStorage(), setup_artwork(harness)
    upload(harness, st, aid)
    with pytest.raises(svc.ImageLimitReachedError):
        upload(harness, st, aid)
    assert len(st.objects) == 2


def test_upload_to_missing_artwork(harness):
    with pytest.raises(svc.ArtworkNotFoundError):
        upload(harness, InMemoryStorage(), uuid.uuid4())


def test_delete_removes_both_objects_and_promotes_next_primary(harness):
    st, aid = InMemoryStorage(), setup_artwork(harness)
    a, b = upload(harness, st, aid), upload(harness, st, aid)
    delete(harness, st, aid, a.id)
    assert not any(k in st.objects for k in (a.storage_key, a.preview_storage_key))
    assert set(st.deleted) == {a.storage_key, a.preview_storage_key}
    remaining = images(harness, aid)
    assert [i.id for i in remaining] == [b.id] and remaining[0].is_primary
    assert artwork(harness, aid).primary_image_url == b.url


@pytest.mark.parametrize(
    "status", [ArtworkStatus.published, ArtworkStatus.reserved, ArtworkStatus.sold]
)
def test_cannot_delete_last_image_of_public_artwork(harness, status):
    st, aid = InMemoryStorage(), setup_artwork(harness, status)
    a = upload(harness, st, aid)
    with pytest.raises(svc.LastImageError):
        delete(harness, st, aid, a.id)
    assert len(images(harness, aid)) == 1 and a.storage_key in st.objects


def test_can_delete_one_of_many_on_published_but_last_on_draft(harness):
    st, aid = InMemoryStorage(), setup_artwork(harness, ArtworkStatus.published)
    a, b = upload(harness, st, aid), upload(harness, st, aid)
    delete(harness, st, aid, b.id)
    with pytest.raises(svc.LastImageError):
        delete(harness, st, aid, a.id)

    draft = setup_artwork(harness)
    d = upload(harness, st, draft)
    delete(harness, st, draft, d.id)
    assert images(harness, draft) == []
    assert artwork(harness, draft).primary_image_url is None


def test_delete_leaves_external_and_other_environment_objects_alone(harness):
    st, aid = InMemoryStorage(key_prefix="staging"), setup_artwork(harness)

    async def seed():
        async with harness.session() as s:
            s.add_all(
                [
                    # external seed image: no keys at all
                    ArtworkImage(
                        artwork_id=aid, url="https://upload.wikimedia.org/x.jpg",
                        sort_order=0, is_primary=True,
                    ),
                    # the other environment's object, in the same bucket
                    ArtworkImage(
                        artwork_id=aid, url=st.public_url("production/artworks/z.webp"),
                        preview_url=st.public_url("production/artworks/zp.webp"),
                        storage_key="production/artworks/z.webp",
                        preview_storage_key="production/artworks/zp.webp", sort_order=1,
                    ),
                    # our prefix but the URL points at a different host
                    ArtworkImage(
                        artwork_id=aid, url="https://evil.example/staging/artworks/q.webp",
                        storage_key="staging/artworks/q.webp", sort_order=2,
                    ),
                ]
            )
            await s.commit()

    harness.run(seed())
    for img in images(harness, aid):
        delete(harness, st, aid, img.id)
    assert st.deleted == []
    assert images(harness, aid) == []


def test_reorder_move_and_primary(harness):
    st, aid = InMemoryStorage(), setup_artwork(harness)
    a, b, c = (upload(harness, st, aid) for _ in range(3))

    async def reorder(ids):
        async with harness.session() as s:
            return await svc.reorder_images(s, aid, ids)

    out = harness.run(reorder([c.id, a.id, b.id]))
    assert [i.id for i in out] == [c.id, a.id, b.id]
    with pytest.raises(svc.InvalidOrderError):
        harness.run(reorder([a.id, b.id]))
    with pytest.raises(svc.InvalidOrderError):
        harness.run(reorder([a.id, a.id, b.id]))

    async def move(i, d):
        async with harness.session() as s:
            return await svc.move_image(s, aid, i, d)

    out = harness.run(move(b.id, -1))
    assert [i.id for i in out] == [c.id, b.id, a.id]

    async def primary(i):
        async with harness.session() as s:
            return await svc.set_primary(s, aid, i)

    out = harness.run(primary(c.id))
    assert [i.id for i in out if i.is_primary] == [c.id]
    assert artwork(harness, aid).primary_image_url == c.url
    with pytest.raises(svc.ImageNotFoundError):
        harness.run(primary(uuid.uuid4()))
