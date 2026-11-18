"""Cloudinary/S3 clients, provider selection, and delete-by-row-provider. Fakes only."""
import boto3
import pytest
from botocore.config import Config
from botocore.stub import Stubber

from app.core.config import settings
from app.models.artwork_image import ArtworkImage
from app.services import artwork_images as svc
from app.services import storage as storage_mod
from app.services.storage import (
    CloudinaryStorage,
    S3Storage,
    StorageError,
    UnconfiguredStorage,
)
from tests.fakes import InMemoryStorage
from tests.test_artwork_images_service import images, setup_artwork, upload


class FakeUploader:
    def __init__(self, destroy_result=None, fail=False):
        self.calls = []
        self.destroy_result = destroy_result or {"result": "ok"}
        self.fail = fail

    def upload(self, file, **options):
        if self.fail:
            raise RuntimeError("boom secret-ish detail")
        self.calls.append(("upload", file.read(), options))
        return {"public_id": options["public_id"]}

    def destroy(self, public_id, **options):
        if self.fail:
            raise RuntimeError("boom")
        self.calls.append(("destroy", public_id, options))
        return self.destroy_result


def cloud(uploader=None, prefix="staging"):
    return CloudinaryStorage(
        cloud_name="demo", api_key="k", api_secret="s", key_prefix=prefix,
        uploader=uploader or FakeUploader(),
    )


def test_cloudinary_put_uses_env_prefixed_public_id_and_builds_url():
    up = FakeUploader()
    url = cloud(up).put("staging/artworks/abc.webp", b"bytes", "image/webp")
    assert url == "https://res.cloudinary.com/demo/image/upload/staging/artworks/abc.webp"
    kind, data, opts = up.calls[0]
    assert data == b"bytes"
    assert opts["public_id"] == "staging/artworks/abc"  # extension stripped, prefix kept
    assert opts["resource_type"] == "image" and opts["overwrite"] is True
    assert opts["cloud_name"] == "demo" and opts["api_secret"] == "s"


def test_cloudinary_delete_tolerates_missing_and_raises_on_failure():
    up = FakeUploader({"result": "not found"})
    cloud(up).delete("staging/artworks/abc.webp")
    assert up.calls[0][1] == "staging/artworks/abc"
    with pytest.raises(StorageError):
        cloud(FakeUploader({"result": "error"})).delete("staging/artworks/abc.webp")
    with pytest.raises(StorageError):
        cloud(FakeUploader(fail=True)).delete("staging/artworks/abc.webp")


def test_cloudinary_put_failure_hides_detail():
    with pytest.raises(StorageError) as e:
        cloud(FakeUploader(fail=True)).put("staging/artworks/a.webp", b"x", "image/webp")
    assert "secret" not in str(e.value)


def test_cloudinary_owns_only_own_env_and_url():
    st = cloud()
    key = "staging/artworks/a.webp"
    assert st.owns(key, st.public_url(key))
    assert not st.owns("production/artworks/a.webp", st.public_url("production/artworks/a.webp"))
    assert not st.owns(key, "https://res.cloudinary.com/other/image/upload/" + key)
    assert not st.owns(key, None)


def test_s3_generic_uses_endpoint_and_region_overrides():
    s = S3Storage(
        bucket="b", public_base_url="https://cdn.x/", endpoint_url="https://minio.local:9000",
        region="eu-west-1", access_key_id="a", secret_access_key="b", key_prefix="staging",
    )
    assert s.provider == "s3"
    assert s._client.meta.endpoint_url == "https://minio.local:9000"
    assert s._client.meta.region_name == "eu-west-1"
    assert s.public_url("staging/artworks/a.webp") == "https://cdn.x/staging/artworks/a.webp"


def test_s3_put_via_stub():
    client = boto3.client(
        "s3", region_name="us-east-1", aws_access_key_id="t", aws_secret_access_key="t",
        config=Config(signature_version="s3v4"),
    )
    with Stubber(client) as stub:
        stub.add_response("put_object", {})
        st = S3Storage(bucket="b", public_base_url="https://c", key_prefix="staging", client=client)
        assert st.put("staging/artworks/a.webp", b"d", "image/webp") == (
            "https://c/staging/artworks/a.webp"
        )


@pytest.fixture
def cfg(monkeypatch):
    for name in (
        "STORAGE_BACKEND", "CLOUDINARY_CLOUD_NAME", "CLOUDINARY_API_KEY", "CLOUDINARY_API_SECRET",
        "R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET",
        "R2_PUBLIC_BASE_URL", "S3_BUCKET", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY",
        "S3_PUBLIC_BASE_URL",
    ):
        monkeypatch.setattr(settings, name, None)
    storage_mod.storage_for_provider.cache_clear()
    yield monkeypatch
    storage_mod.storage_for_provider.cache_clear()


def set_cloudinary(mp):
    mp.setattr(settings, "CLOUDINARY_CLOUD_NAME", "demo")
    mp.setattr(settings, "CLOUDINARY_API_KEY", "k")
    mp.setattr(settings, "CLOUDINARY_API_SECRET", "s")


def set_r2(mp):
    for n, v in (("R2_ACCOUNT_ID", "acct"), ("R2_ACCESS_KEY_ID", "a"),
                 ("R2_SECRET_ACCESS_KEY", "b"), ("R2_BUCKET", "bk"),
                 ("R2_PUBLIC_BASE_URL", "https://r2.example")):
        mp.setattr(settings, n, v)


def test_nothing_configured_is_unconfigured(cfg):
    assert isinstance(storage_mod.get_image_storage(), UnconfiguredStorage)


def test_default_is_whichever_is_configured(cfg):
    set_cloudinary(cfg)
    assert storage_mod.get_image_storage().provider == "cloudinary"
    storage_mod.storage_for_provider.cache_clear()
    cfg.setattr(settings, "CLOUDINARY_API_KEY", None)
    set_r2(cfg)
    assert storage_mod.get_image_storage().provider == "r2"


def test_explicit_backend_wins_and_missing_creds_mean_unconfigured(cfg):
    set_cloudinary(cfg)
    set_r2(cfg)
    cfg.setattr(settings, "STORAGE_BACKEND", "cloudinary")
    assert storage_mod.get_image_storage().provider == "cloudinary"
    storage_mod.storage_for_provider.cache_clear()
    cfg.setattr(settings, "STORAGE_BACKEND", "s3")  # chosen but not configured
    assert not storage_mod.get_image_storage().configured


def test_upload_records_provider_and_url_from_that_provider(harness):
    st = InMemoryStorage(provider="cloudinary", base="https://res.cloudinary.test")
    aid = setup_artwork(harness)
    img = upload(harness, st, aid)
    assert img.storage_provider == "cloudinary"
    assert img.url.startswith("https://res.cloudinary.test/staging/artworks/")


def test_delete_uses_the_rows_own_provider_after_switching_backend(harness):
    old = InMemoryStorage(provider="r2", base="https://r2.test")
    new = InMemoryStorage(provider="cloudinary", base="https://res.cloudinary.test")
    aid = setup_artwork(harness)
    a = upload(harness, old, aid)
    b = upload(harness, new, aid)

    async def go(image_id):
        async with harness.session() as s:
            await svc.delete_image(
                s, new, aid, image_id, resolve_storage=lambda p: {"r2": old}[p]
            )

    harness.run(go(a.id))
    assert set(old.deleted) == {a.storage_key, a.preview_storage_key}
    assert new.deleted == []  # the active provider was not asked to delete r2's objects
    harness.run(go(b.id))
    assert set(new.deleted) == {b.storage_key, b.preview_storage_key}
    assert images(harness, aid) == []


def test_delete_skips_rows_without_provider(harness):
    st = InMemoryStorage()
    aid = setup_artwork(harness)

    async def seed():
        async with harness.session() as s:
            s.add(ArtworkImage(
                artwork_id=aid, url=st.public_url("staging/artworks/x.webp"),
                storage_key="staging/artworks/x.webp", sort_order=0, is_primary=True,
            ))
            await s.commit()

    harness.run(seed())
    (img,) = images(harness, aid)

    async def go():
        async with harness.session() as s:
            await svc.delete_image(s, st, aid, img.id)

    harness.run(go())
    assert st.deleted == []
