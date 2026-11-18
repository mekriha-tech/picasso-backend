"""Object storage for artwork images, behind a small interface.

Providers: Cloudinary (official SDK), Cloudflare R2 and generic S3 (both boto3's S3 client).
Which one receives NEW uploads is `STORAGE_BACKEND`; each image row remembers its own
`storage_provider`, so rows on an older provider still delete correctly after a switch
(`storage_for_provider`). Tests inject an in-memory fake (tests/fakes.py) so nothing touches the
network. When nothing is configured the app still boots; `get_image_storage()` then returns an
`UnconfiguredStorage` whose operations raise `StorageNotConfiguredError` (the API's 503).

Keys have the same shape on every provider, `<environment>/artworks/<random>.webp`, so moving an
image between providers keeps its key (Cloudinary strips the extension to form its public_id).
"""
import asyncio
import io
import logging
import re
from functools import lru_cache
from typing import Protocol

from app.core.config import settings

logger = logging.getLogger(__name__)

# Keys are random and never repeat, so the objects are safe to cache forever.
CACHE_CONTROL = "public, max-age=31536000, immutable"

PROVIDERS = ("cloudinary", "r2", "s3")


class StorageNotConfiguredError(Exception):
    pass


class StorageError(Exception):
    """The storage backend rejected or failed a request."""


def environment_prefix(environment: str) -> str:
    """Key prefix for this deployment, e.g. "production" or "staging". Production and staging
    may share one bucket/account, so every object lives under its own environment's prefix."""
    prefix = re.sub(r"[^a-z0-9-]+", "-", environment.lower()).strip("-")
    return prefix or "unknown"


class ImageStorage(Protocol):
    configured: bool
    provider: str | None  # value stored in artwork_images.storage_provider
    key_prefix: str
    identity: str  # which account/bucket this is; equal identities are the same physical store

    def put(self, key: str, data: bytes, content_type: str) -> str:
        """Store `data` under `key` and return its public URL."""

    def delete(self, key: str) -> None:
        """Remove `key`. Removing a key that does not exist is not an error."""

    def public_url(self, key: str) -> str: ...

    def owns(self, key: str, url: str | None) -> bool:
        """True only if `key` is under this environment's prefix and `url` is the public URL
        of that key in our store. Anything else (external URLs, other environments'
        objects) must never be deleted."""


class UnconfiguredStorage:
    configured = False
    provider = None
    key_prefix = ""
    identity = ""

    def owns(self, key: str, url: str | None) -> bool:
        raise StorageNotConfiguredError("Image storage is not configured.")

    def put(self, key: str, data: bytes, content_type: str) -> str:
        raise StorageNotConfiguredError("Image storage is not configured.")

    def delete(self, key: str) -> None:
        raise StorageNotConfiguredError("Image storage is not configured.")

    def public_url(self, key: str) -> str:
        raise StorageNotConfiguredError("Image storage is not configured.")


class S3Storage:
    """S3-compatible object storage (AWS S3, Cloudflare R2, MinIO, ...) via boto3."""

    configured = True
    provider = "s3"

    def __init__(
        self,
        *,
        bucket: str,
        public_base_url: str,
        endpoint_url: str | None = None,
        region: str | None = None,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
        key_prefix: str = "unknown",
        client=None,
    ):
        self.key_prefix = key_prefix
        self._bucket = bucket
        self._public_base_url = public_base_url.rstrip("/")
        self.identity = f"s3:{endpoint_url or ''}:{bucket}"  # R2 and generic S3 share a namespace
        if client is None:
            import boto3
            from botocore.config import Config

            client = boto3.client(
                "s3",
                endpoint_url=endpoint_url or None,
                aws_access_key_id=access_key_id,
                aws_secret_access_key=secret_access_key,
                region_name=region or None,
                config=Config(
                    signature_version="s3v4",
                    retries={"max_attempts": 3, "mode": "standard"},
                    connect_timeout=5,
                    read_timeout=30,
                    # R2 does not support the default CRC checksums newer botocore adds.
                    request_checksum_calculation="when_required",
                    response_checksum_validation="when_required",
                ),
            )
        self._client = client

    def public_url(self, key: str) -> str:
        return f"{self._public_base_url}/{key}"

    def owns(self, key: str, url: str | None) -> bool:
        return (
            key.startswith(f"{self.key_prefix}/")
            and ".." not in key
            and url == self.public_url(key)
        )

    def put(self, key: str, data: bytes, content_type: str) -> str:
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=data,
                ContentType=content_type,
                CacheControl=CACHE_CONTROL,
            )
        except (BotoCoreError, ClientError) as exc:
            logger.error("%s put_object failed for key %s: %s", self.provider, key, type(exc).__name__)
            raise StorageError("Could not store the image.") from exc
        return self.public_url(key)

    def delete(self, key: str) -> None:
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            self._client.delete_object(Bucket=self._bucket, Key=key)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("NoSuchKey", "404", "NotFound"):
                return  # already gone: deleting is idempotent
            logger.error("%s delete_object failed for key %s: %s", self.provider, key, type(exc).__name__)
            raise StorageError("Could not delete the image from storage.") from exc
        except BotoCoreError as exc:
            logger.error("%s delete_object failed for key %s: %s", self.provider, key, type(exc).__name__)
            raise StorageError("Could not delete the image from storage.") from exc


class R2Storage(S3Storage):
    """Cloudflare R2: the S3 client pointed at the account's endpoint, region "auto"."""

    provider = "r2"

    def __init__(self, *, account_id: str | None = None, client=None, **kwargs):
        kwargs["endpoint_url"] = f"https://{account_id}.r2.cloudflarestorage.com"
        kwargs["region"] = "auto"
        super().__init__(client=client, **kwargs)


class CloudinaryStorage:
    """Cloudinary via the official SDK.

    The key `staging/artworks/abc.webp` becomes public_id `staging/artworks/abc` (Cloudinary
    addresses assets without the extension) and is delivered at
    `https://res.cloudinary.com/<cloud>/image/upload/staging/artworks/abc.webp`. The URL is
    built by us from the key, never read from the API response, so it is predictable and the
    same function serves `owns()`.
    """

    configured = True
    provider = "cloudinary"

    def __init__(
        self,
        *,
        cloud_name: str,
        api_key: str,
        api_secret: str,
        key_prefix: str = "unknown",
        uploader=None,
    ):
        self.key_prefix = key_prefix
        self.identity = f"cloudinary:{cloud_name}"
        self._cloud_name = cloud_name
        # Credentials are passed per call instead of cloudinary.config(), which is global state.
        self._auth = {"cloud_name": cloud_name, "api_key": api_key, "api_secret": api_secret}
        if uploader is None:
            import cloudinary.uploader as uploader
        self._uploader = uploader

    @staticmethod
    def _public_id(key: str) -> str:
        head, _, name = key.rpartition("/")
        stem = name.rsplit(".", 1)[0]
        return f"{head}/{stem}" if head else stem

    def public_url(self, key: str) -> str:
        return f"https://res.cloudinary.com/{self._cloud_name}/image/upload/{key}"

    def owns(self, key: str, url: str | None) -> bool:
        return (
            key.startswith(f"{self.key_prefix}/")
            and ".." not in key
            and url == self.public_url(key)
        )

    def put(self, key: str, data: bytes, content_type: str) -> str:
        try:
            self._uploader.upload(
                io.BytesIO(data),
                public_id=self._public_id(key),
                resource_type="image",
                type="upload",
                overwrite=True,  # re-running a copy must be harmless
                unique_filename=False,
                use_filename=False,
                invalidate=True,
                **self._auth,
            )
        except Exception as exc:  # SDK raises cloudinary.exceptions.Error, plus network errors
            logger.error("Cloudinary upload failed for key %s: %s", key, type(exc).__name__)
            raise StorageError("Could not store the image.") from exc
        return self.public_url(key)

    def delete(self, key: str) -> None:
        try:
            result = self._uploader.destroy(
                self._public_id(key),
                resource_type="image",
                type="upload",
                invalidate=True,
                **self._auth,
            )
        except Exception as exc:
            logger.error("Cloudinary destroy failed for key %s: %s", key, type(exc).__name__)
            raise StorageError("Could not delete the image from storage.") from exc
        outcome = (result or {}).get("result")
        if outcome not in ("ok", "not found"):  # "not found": already gone, which is fine
            logger.error("Cloudinary destroy returned %r for key %s", outcome, key)
            raise StorageError("Could not delete the image from storage.")


def build_storage(provider: str) -> ImageStorage:
    """Storage for `provider` from settings, or UnconfiguredStorage if its variables are unset."""
    prefix = environment_prefix(settings.ENVIRONMENT)
    if provider == "cloudinary" and settings.cloudinary_configured:
        return CloudinaryStorage(
            cloud_name=settings.CLOUDINARY_CLOUD_NAME,
            api_key=settings.CLOUDINARY_API_KEY,
            api_secret=settings.CLOUDINARY_API_SECRET,
            key_prefix=prefix,
        )
    if provider == "r2" and settings.r2_configured:
        return R2Storage(
            bucket=settings.R2_BUCKET,
            public_base_url=settings.R2_PUBLIC_BASE_URL,
            account_id=settings.R2_ACCOUNT_ID,
            access_key_id=settings.R2_ACCESS_KEY_ID,
            secret_access_key=settings.R2_SECRET_ACCESS_KEY,
            key_prefix=prefix,
        )
    if provider == "s3" and settings.s3_configured:
        return S3Storage(
            bucket=settings.S3_BUCKET,
            public_base_url=settings.S3_PUBLIC_BASE_URL,
            endpoint_url=settings.S3_ENDPOINT_URL,
            region=settings.S3_REGION,
            access_key_id=settings.S3_ACCESS_KEY_ID,
            secret_access_key=settings.S3_SECRET_ACCESS_KEY,
            key_prefix=prefix,
        )
    return UnconfiguredStorage()


def active_provider() -> str | None:
    """Provider for new uploads: STORAGE_BACKEND if set, else whichever is configured (r2, then
    cloudinary, then s3, so an existing deployment keeps uploading where it did)."""
    if settings.STORAGE_BACKEND:
        return settings.STORAGE_BACKEND
    configured = [
        name
        for name, ok in (
            ("r2", settings.r2_configured),
            ("cloudinary", settings.cloudinary_configured),
            ("s3", settings.s3_configured),
        )
        if ok
    ]
    if len(configured) > 1:
        logger.warning(
            "Several storage providers are configured (%s) and STORAGE_BACKEND is unset; "
            "using %s. Set STORAGE_BACKEND explicitly.",
            configured,
            configured[0],
        )
    return configured[0] if configured else None


@lru_cache(maxsize=None)
def storage_for_provider(provider: str) -> ImageStorage:
    """Storage for a specific provider. Deleting an image uses this with the row's own provider,
    whatever STORAGE_BACKEND currently says."""
    return build_storage(provider)


def get_image_storage() -> ImageStorage:
    """FastAPI dependency (new uploads). Tests override it via app.dependency_overrides."""
    provider = active_provider()
    return storage_for_provider(provider) if provider else UnconfiguredStorage()


async def storage_put(storage: ImageStorage, key: str, data: bytes, content_type: str) -> str:
    # The SDKs are blocking; keep them off the event loop.
    return await asyncio.to_thread(storage.put, key, data, content_type)


async def storage_delete(storage: ImageStorage, key: str) -> None:
    await asyncio.to_thread(storage.delete, key)
