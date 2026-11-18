"""Object storage for artwork images, behind a small interface.

Production uses Cloudflare R2 through boto3's S3-compatible client. Tests inject an in-memory
fake (tests/fakes.py) so nothing touches the network. When R2 is not configured the app still
boots; `get_image_storage()` then returns an `UnconfiguredStorage` whose operations raise
`StorageNotConfiguredError`, which the API turns into a 503.
"""
import asyncio
import logging
import re
from functools import lru_cache
from typing import Protocol

from app.core.config import settings

logger = logging.getLogger(__name__)

# Keys are random and never repeat, so the objects are safe to cache forever.
CACHE_CONTROL = "public, max-age=31536000, immutable"


class StorageNotConfiguredError(Exception):
    pass


class StorageError(Exception):
    """The storage backend rejected or failed a request."""


def environment_prefix(environment: str) -> str:
    """Key prefix for this deployment, e.g. "production" or "staging". Production and staging
    share one bucket, so every object lives under its own environment's prefix."""
    prefix = re.sub(r"[^a-z0-9-]+", "-", environment.lower()).strip("-")
    return prefix or "unknown"


class ImageStorage(Protocol):
    configured: bool
    key_prefix: str

    def put(self, key: str, data: bytes, content_type: str) -> str:
        """Store `data` under `key` and return its public URL."""

    def delete(self, key: str) -> None:
        """Remove `key`. Removing a key that does not exist is not an error."""

    def public_url(self, key: str) -> str: ...

    def owns(self, key: str, url: str | None) -> bool:
        """True only if `key` is under this environment's prefix and `url` is the public URL
        of that key in our bucket. Anything else (external URLs, other environments'
        objects) must never be deleted."""


class UnconfiguredStorage:
    configured = False
    key_prefix = ""

    def owns(self, key: str, url: str | None) -> bool:
        raise StorageNotConfiguredError("Image storage is not configured.")

    def put(self, key: str, data: bytes, content_type: str) -> str:
        raise StorageNotConfiguredError("Image storage is not configured.")

    def delete(self, key: str) -> None:
        raise StorageNotConfiguredError("Image storage is not configured.")

    def public_url(self, key: str) -> str:
        raise StorageNotConfiguredError("Image storage is not configured.")


class R2Storage:
    configured = True

    def __init__(
        self,
        *,
        bucket: str,
        public_base_url: str,
        account_id: str | None = None,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
        key_prefix: str = "unknown",
        client=None,
    ):
        self.key_prefix = key_prefix
        self._bucket = bucket
        self._public_base_url = public_base_url.rstrip("/")
        if client is None:
            import boto3
            from botocore.config import Config

            client = boto3.client(
                "s3",
                endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
                aws_access_key_id=access_key_id,
                aws_secret_access_key=secret_access_key,
                region_name="auto",
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
            logger.error("R2 put_object failed for key %s: %s", key, type(exc).__name__)
            raise StorageError("Could not store the image.") from exc
        return self.public_url(key)

    def delete(self, key: str) -> None:
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            self._client.delete_object(Bucket=self._bucket, Key=key)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("NoSuchKey", "404", "NotFound"):
                return  # already gone: deleting is idempotent
            logger.error("R2 delete_object failed for key %s: %s", key, type(exc).__name__)
            raise StorageError("Could not delete the image from storage.") from exc
        except BotoCoreError as exc:
            logger.error("R2 delete_object failed for key %s: %s", key, type(exc).__name__)
            raise StorageError("Could not delete the image from storage.") from exc


@lru_cache(maxsize=1)
def _build_default() -> ImageStorage:
    if not settings.r2_configured:
        return UnconfiguredStorage()
    return R2Storage(
        bucket=settings.R2_BUCKET,
        public_base_url=settings.R2_PUBLIC_BASE_URL,
        account_id=settings.R2_ACCOUNT_ID,
        access_key_id=settings.R2_ACCESS_KEY_ID,
        secret_access_key=settings.R2_SECRET_ACCESS_KEY,
        key_prefix=environment_prefix(settings.ENVIRONMENT),
    )


def get_image_storage() -> ImageStorage:
    """FastAPI dependency. Tests override it via app.dependency_overrides."""
    return _build_default()


async def storage_put(storage: ImageStorage, key: str, data: bytes, content_type: str) -> str:
    # boto3 is blocking; keep it off the event loop.
    return await asyncio.to_thread(storage.put, key, data, content_type)


async def storage_delete(storage: ImageStorage, key: str) -> None:
    await asyncio.to_thread(storage.delete, key)
