"""Artwork image management: upload, delete, reorder, set primary.

Every mutation takes a row lock on the artwork first, so concurrent uploads/deletes on the same
artwork serialise and sort_order / is_primary stay consistent (the partial unique index
`one_primary_image_per_artwork` is the backstop).

PRD rule 10: an artwork that is published, reserved or sold must keep at least one image.
`artworks.primary_image_url` is kept equal to the primary image's full-size URL.
"""
import asyncio
import logging
import secrets
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.artwork import Artwork
from app.models.artwork_image import ArtworkImage
from app.services import image_processing
from app.services.artworks import PUBLIC_ARTWORK_STATUSES
from app.services.storage import ImageStorage, StorageNotConfiguredError, storage_delete, storage_put

logger = logging.getLogger(__name__)

MAX_IMAGES_PER_ARTWORK = 12
MAX_ALT_TEXT_LENGTH = 300

ARTWORK_NOT_FOUND = "Artwork not found"
IMAGE_NOT_FOUND = "Image not found"


class ArtworkNotFoundError(Exception):
    pass


class ImageNotFoundError(Exception):
    pass


class ImageLimitReachedError(Exception):
    pass


class LastImageError(Exception):
    """Removing this image would leave a published/reserved/sold artwork with none (rule 10)."""


class InvalidOrderError(Exception):
    pass


async def _lock_artwork(db: AsyncSession, artwork_id: uuid.UUID) -> Artwork:
    result = await db.execute(
        select(Artwork)
        .where(Artwork.id == artwork_id, Artwork.deleted_at.is_(None))
        .with_for_update()
    )
    artwork = result.scalars().first()
    if artwork is None:
        raise ArtworkNotFoundError(ARTWORK_NOT_FOUND)
    return artwork


async def list_images(db: AsyncSession, artwork_id: uuid.UUID) -> list[ArtworkImage]:
    result = await db.execute(
        select(ArtworkImage)
        .where(ArtworkImage.artwork_id == artwork_id)
        .order_by(ArtworkImage.sort_order, ArtworkImage.id)
    )
    return list(result.scalars().all())


def _random_key(storage: ImageStorage) -> str:
    # Environment prefix (shared bucket) + 128 random bits; never derived from the filename.
    return f"{storage.key_prefix}/artworks/{secrets.token_hex(16)}.webp"


def _sync_primary_url(artwork: Artwork, images: list[ArtworkImage]) -> None:
    primary = next((i for i in images if i.is_primary), None)
    artwork.primary_image_url = primary.url if primary else None


async def add_image(
    db: AsyncSession,
    storage: ImageStorage,
    artwork_id: uuid.UUID,
    data: bytes,
    alt_text: str | None = None,
) -> ArtworkImage:
    if not storage.configured:
        raise StorageNotConfiguredError("Image storage is not configured.")

    # Decode, validate and re-encode before touching the DB lock or storage. CPU-bound, so it
    # runs in a worker thread. Raises InvalidImageError / ImageTooLargeError.
    processed = await asyncio.to_thread(image_processing.process_image, data)

    alt = (alt_text or "").strip()[:MAX_ALT_TEXT_LENGTH] or None

    artwork = await _lock_artwork(db, artwork_id)
    existing = await list_images(db, artwork.id)
    if len(existing) >= MAX_IMAGES_PER_ARTWORK:
        await db.rollback()
        raise ImageLimitReachedError(
            f"An artwork can have at most {MAX_IMAGES_PER_ARTWORK} images."
        )

    full_key, preview_key = _random_key(storage), _random_key(storage)
    uploaded: list[str] = []
    try:
        full_url = await storage_put(storage, full_key, processed.full, processed.content_type)
        uploaded.append(full_key)
        preview_url = await storage_put(
            storage, preview_key, processed.preview, processed.content_type
        )
        uploaded.append(preview_key)

        image = ArtworkImage(
            artwork_id=artwork.id,
            url=full_url,
            preview_url=preview_url,
            storage_key=full_key,
            preview_storage_key=preview_key,
            alt_text=alt,
            sort_order=(max((i.sort_order for i in existing), default=-1) + 1),
            is_primary=not existing,
            width_px=processed.width,
            height_px=processed.height,
        )
        db.add(image)
        if image.is_primary:
            artwork.primary_image_url = image.url
        await db.commit()
    except Exception:
        await db.rollback()
        for key in uploaded:  # don't leave orphaned objects behind
            try:
                await storage_delete(storage, key)
            except Exception:
                logger.warning("Could not clean up orphaned object %s", key)
        raise
    await db.refresh(image)
    return image


async def delete_image(
    db: AsyncSession, storage: ImageStorage, artwork_id: uuid.UUID, image_id: uuid.UUID
) -> None:
    artwork = await _lock_artwork(db, artwork_id)
    images = await list_images(db, artwork.id)
    target = next((i for i in images if i.id == image_id), None)
    if target is None:
        await db.rollback()
        raise ImageNotFoundError(IMAGE_NOT_FOUND)

    if len(images) == 1 and artwork.status.value in PUBLIC_ARTWORK_STATUSES:
        await db.rollback()
        raise LastImageError(
            "Artwork needs at least one image while it is published, reserved or sold."
        )

    # Storage first: object deletes are idempotent, so if one fails the row is still there and
    # the admin can simply retry. (Row first would orphan objects with no way to find them.)
    try:
        for key, url in (
            (target.storage_key, target.url),
            (target.preview_storage_key, target.preview_url),
        ):
            # Only objects in our bucket under this environment's prefix. External URLs
            # (seed images) and other environments' objects are left alone.
            if key and storage.owns(key, url):
                await storage_delete(storage, key)
    except Exception:
        await db.rollback()
        raise

    remaining = [i for i in images if i.id != target.id]
    was_primary = target.is_primary
    await db.delete(target)
    if was_primary and remaining:
        await db.flush()  # free the unique-primary slot before promoting another image
        remaining[0].is_primary = True
    _sync_primary_url(artwork, remaining)
    await db.commit()


async def reorder_images(
    db: AsyncSession, artwork_id: uuid.UUID, ordered_ids: list[uuid.UUID]
) -> list[ArtworkImage]:
    artwork = await _lock_artwork(db, artwork_id)
    images = await list_images(db, artwork.id)
    if len(ordered_ids) != len(set(ordered_ids)) or set(ordered_ids) != {i.id for i in images}:
        await db.rollback()
        raise InvalidOrderError("image_ids must list every image of this artwork exactly once.")
    by_id = {i.id: i for i in images}
    for position, image_id in enumerate(ordered_ids):
        by_id[image_id].sort_order = position
    await db.commit()
    return await list_images(db, artwork_id)


async def move_image(
    db: AsyncSession, artwork_id: uuid.UUID, image_id: uuid.UUID, direction: int
) -> list[ArtworkImage]:
    """Swap an image with its neighbour (direction -1 = earlier, +1 = later)."""
    artwork = await _lock_artwork(db, artwork_id)
    images = await list_images(db, artwork.id)
    ids = [i.id for i in images]
    if image_id not in ids:
        await db.rollback()
        raise ImageNotFoundError(IMAGE_NOT_FOUND)
    index = ids.index(image_id)
    swap = index + direction
    if 0 <= swap < len(ids):
        ids[index], ids[swap] = ids[swap], ids[index]
    for position, i in enumerate(sorted_by(images, ids)):
        i.sort_order = position
    await db.commit()
    return await list_images(db, artwork_id)


def sorted_by(images: list[ArtworkImage], ids: list[uuid.UUID]) -> list[ArtworkImage]:
    by_id = {i.id: i for i in images}
    return [by_id[i] for i in ids]


async def set_primary(
    db: AsyncSession, artwork_id: uuid.UUID, image_id: uuid.UUID
) -> list[ArtworkImage]:
    artwork = await _lock_artwork(db, artwork_id)
    images = await list_images(db, artwork.id)
    target = next((i for i in images if i.id == image_id), None)
    if target is None:
        await db.rollback()
        raise ImageNotFoundError(IMAGE_NOT_FOUND)
    if not target.is_primary:
        for image in images:
            if image.is_primary:
                image.is_primary = False
        await db.flush()  # clear the old primary before setting the new one (unique index)
        target.is_primary = True
    _sync_primary_url(artwork, images)
    await db.commit()
    return await list_images(db, artwork_id)
