"""R2Storage against botocore's Stubber: checks request shapes with no network or credentials."""
import boto3
import pytest
from botocore.config import Config
from botocore.stub import Stubber

from app.core.config import settings
from app.services import storage as storage_mod
from app.services.storage import (
    CACHE_CONTROL,
    R2Storage,
    StorageError,
    UnconfiguredStorage,
    environment_prefix,
)


@pytest.fixture
def r2():
    client = boto3.client(
        "s3", region_name="auto", endpoint_url="https://acct.r2.cloudflarestorage.com",
        aws_access_key_id="test", aws_secret_access_key="test",
        config=Config(signature_version="s3v4"),
    )
    with Stubber(client) as stub:
        yield R2Storage(
            bucket="b", public_base_url="https://cdn.example.test/", key_prefix="staging",
            client=client,
        ), stub


def test_put_sends_cache_headers_and_returns_public_url(r2):
    st, stub = r2
    stub.add_response(
        "put_object", {},
        {"Bucket": "b", "Key": "staging/artworks/k.webp", "Body": b"d",
         "ContentType": "image/webp", "CacheControl": CACHE_CONTROL},
    )
    assert st.put("staging/artworks/k.webp", b"d", "image/webp") == (
        "https://cdn.example.test/staging/artworks/k.webp"
    )
    stub.assert_no_pending_responses()


def test_put_failure_is_storage_error(r2):
    st, stub = r2
    stub.add_client_error("put_object", service_error_code="AccessDenied")
    with pytest.raises(StorageError):
        st.put("staging/artworks/k.webp", b"d", "image/webp")


def test_delete_missing_object_is_not_an_error(r2):
    st, stub = r2
    stub.add_client_error("delete_object", service_error_code="NoSuchKey", http_status_code=404)
    st.delete("staging/artworks/gone.webp")


def test_delete_other_failures_raise(r2):
    st, stub = r2
    stub.add_client_error("delete_object", service_error_code="AccessDenied", http_status_code=403)
    with pytest.raises(StorageError):
        st.delete("staging/artworks/k.webp")


def test_owns_requires_prefix_and_exact_url(r2):
    st, _ = r2
    key = "staging/artworks/k.webp"
    assert st.owns(key, "https://cdn.example.test/" + key)
    assert not st.owns(key, "https://other.test/" + key)
    assert not st.owns("production/artworks/k.webp", "https://cdn.example.test/production/artworks/k.webp")
    assert not st.owns("staging/../production/k.webp", "https://cdn.example.test/staging/../production/k.webp")


def test_environment_prefix():
    assert environment_prefix("Production") == "production"
    assert environment_prefix(" staging!! ") == "staging"
    assert environment_prefix("") == "unknown"


def test_unset_r2_settings_give_unconfigured_storage(monkeypatch):
    for name in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET", "R2_PUBLIC_BASE_URL"):
        monkeypatch.setattr(settings, name, None)
    storage_mod._build_default.cache_clear()
    try:
        assert isinstance(storage_mod.get_image_storage(), UnconfiguredStorage)
    finally:
        storage_mod._build_default.cache_clear()
