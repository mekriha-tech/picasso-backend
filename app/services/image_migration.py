"""Move artwork images from one storage provider to another (see docs/STORAGE.md).

Safety properties:
- A row is only updated after BOTH of its objects were copied, and each row is committed on its
  own, so an interrupted run loses nothing and re-running continues where it stopped (migrated
  rows no longer match the "from" provider).
- Copying is a plain overwrite under the same key, so repeating it is harmless.
- Rows with no storage_provider (external URLs) are never touched.
- Source objects are only deleted in the explicit delete pass, and only after the copy is
  verified by fetching it back from the destination's public URL.
"""
import asyncio
import logging
from dataclasses import dataclass, field
from typing import Callable

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.artwork import Artwork
from app.models.artwork_image import ArtworkImage
from app.services.storage import ImageStorage, StorageError, storage_delete, storage_put

logger = logging.getLogger(__name__)

DEFAULT_CONTENT_TYPE = "image/webp"


class MigrationRefused(Exception):
    pass


@dataclass
class Fetched:
    data: bytes
    content_type: str


Fetcher = Callable[[str], Fetched]


@dataclass
class Report:
    dry_run: bool = False
    images_scanned: int = 0  # rows on the source provider
    images_migrated: int = 0  # rows moved (or that would be, in a dry run)
    objects_copied: int = 0
    skipped_external: int = 0  # rows with no storage_provider
    skipped_not_owned: int = 0  # on the source provider but not ours (other env / foreign URL)
    skipped_other_provider: int = 0  # rows on a provider that is neither source nor destination
    already_on_destination: int = 0
    source_objects_deleted: int = 0
    errors: list[str] = field(default_factory=list)

    def lines(self) -> list[str]:
        tag = "DRY RUN - nothing was changed. " if self.dry_run else ""
        migrated = "would migrate" if self.dry_run else "migrated"
        deleted = "would delete" if self.dry_run else "deleted"
        out = [
            f"{tag}images on source provider:        {self.images_scanned}",
            f"images {migrated}:                 {self.images_migrated}",
            f"objects copied:                       {self.objects_copied}",
            f"source objects {deleted}:           {self.source_objects_deleted}",
            f"already on destination:               {self.already_on_destination}",
            f"skipped (external URL, no provider):  {self.skipped_external}",
            f"skipped (not owned by this env):      {self.skipped_not_owned}",
            f"skipped (other provider):             {self.skipped_other_provider}",
            f"errors:                               {len(self.errors)}",
        ]
        out += [f"  ! {e}" for e in self.errors]
        return out


def default_fetcher(url: str) -> Fetched:
    """Download a public image URL. Bounded in size and time; only http(s)."""
    import httpx

    if not url.startswith(("https://", "http://")):
        raise StorageError(f"Refusing to fetch non-http URL: {url[:60]}")
    max_bytes = 30 * 1024 * 1024
    with httpx.stream("GET", url, timeout=30, follow_redirects=True) as resp:
        resp.raise_for_status()
        content_type = resp.headers.get("content-type", "").split(";")[0].strip()
        if not content_type.startswith("image/"):
            raise StorageError(f"Unexpected content type {content_type!r} from {url[:80]}")
        data = bytearray()
        for chunk in resp.iter_bytes():
            data += chunk
            if len(data) > max_bytes:
                raise StorageError(f"Object larger than {max_bytes} bytes: {url[:80]}")
    if not data:
        raise StorageError(f"Empty response from {url[:80]}")
    return Fetched(bytes(data), content_type)


def _object_refs(image: ArtworkImage) -> list[tuple[str, str]]:
    """(key, url) of every stored object of the row, full size first."""
    refs = []
    if image.storage_key:
        refs.append((image.storage_key, image.url))
    if image.preview_storage_key:
        refs.append((image.preview_storage_key, image.preview_url or ""))
    return refs


async def _copy_object(
    source: ImageStorage, dest: ImageStorage, key: str, url: str, fetch: Fetcher
) -> str:
    fetched = await asyncio.to_thread(fetch, url)
    return await storage_put(dest, key, fetched.data, fetched.content_type or DEFAULT_CONTENT_TYPE)


async def migrate_images(
    db: AsyncSession,
    source: ImageStorage,
    dest: ImageStorage,
    *,
    dry_run: bool = False,
    delete_source: bool = False,
    fetch: Fetcher = default_fetcher,
) -> Report:
    if not source.configured or not dest.configured:
        raise MigrationRefused("Both the source and the destination provider must be configured.")
    if source.provider == dest.provider or source.identity == dest.identity:
        raise MigrationRefused(
            "Source and destination are the same store; copying (and --delete-source) would "
            "destroy the data."
        )
    report = Report(dry_run=dry_run)

    report.skipped_external = (
        await db.execute(
            select(func.count()).select_from(ArtworkImage).where(ArtworkImage.storage_provider.is_(None))
        )
    ).scalar_one()
    report.skipped_other_provider = (
        await db.execute(
            select(func.count())
            .select_from(ArtworkImage)
            .where(
                ArtworkImage.storage_provider.is_not(None),
                ArtworkImage.storage_provider.not_in([source.provider, dest.provider]),
            )
        )
    ).scalar_one()
    report.already_on_destination = (
        await db.execute(
            select(func.count())
            .select_from(ArtworkImage)
            .where(ArtworkImage.storage_provider == dest.provider)
        )
    ).scalar_one()

    # Phase 1: copy. Ids first so the loop does not hold one long result set across commits.
    ids = (
        await db.execute(
            select(ArtworkImage.id)
            .where(ArtworkImage.storage_provider == source.provider)
            .order_by(ArtworkImage.id)
        )
    ).scalars().all()
    report.images_scanned = len(ids)

    for image_id in ids:
        image = await db.get(ArtworkImage, image_id)
        refs = _object_refs(image)
        if not refs or not all(source.owns(key, url) for key, url in refs):
            report.skipped_not_owned += 1
            continue
        if dry_run:
            report.images_migrated += 1
            report.objects_copied += len(refs)
            continue
        try:
            new_urls = [await _copy_object(source, dest, k, u, fetch) for k, u in refs]
        except Exception as exc:
            await db.rollback()
            report.errors.append(f"image {image_id}: copy failed ({type(exc).__name__}: {exc})")
            continue

        old_url = image.url
        image.storage_provider = dest.provider
        image.url = new_urls[0]
        if image.preview_storage_key:
            image.preview_url = new_urls[1]
        if image.is_primary:
            artwork = await db.get(Artwork, image.artwork_id)
            if artwork is not None and artwork.primary_image_url == old_url:
                artwork.primary_image_url = image.url
        try:
            await db.commit()
        except Exception as exc:
            await db.rollback()
            report.errors.append(f"image {image_id}: row update failed ({type(exc).__name__})")
            continue
        report.images_migrated += 1
        report.objects_copied += len(refs)

    # Phase 2 (explicit): remove the old objects of every row now on the destination, including
    # rows migrated by earlier runs. Keys are identical on both providers, so the source object
    # is found by key alone.
    if delete_source:
        dest_ids = (
            await db.execute(
                select(ArtworkImage.id)
                .where(
                    ArtworkImage.storage_provider == dest.provider,
                    ArtworkImage.storage_key.is_not(None),
                )
                .order_by(ArtworkImage.id)
            )
        ).scalars().all()
        for image_id in dest_ids:
            image = await db.get(ArtworkImage, image_id)
            refs = _object_refs(image)
            if not all(dest.owns(k, u) for k, u in refs):
                continue  # not ours on the destination, so not ours to remove from the source
            for key, url in refs:
                if not source.owns(key, source.public_url(key)):
                    continue  # other environment's prefix on the source: hands off
                if dry_run:
                    report.source_objects_deleted += 1
                    continue
                try:
                    await asyncio.to_thread(fetch, url)  # destination copy must be readable
                    await storage_delete(source, key)
                except Exception as exc:
                    report.errors.append(
                        f"image {image_id}: source delete skipped for {key} "
                        f"({type(exc).__name__}: {exc})"
                    )
                    continue
                report.source_objects_deleted += 1
            await db.rollback()  # read-only pass: release the transaction between rows

    return report
