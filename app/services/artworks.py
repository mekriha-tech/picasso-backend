import uuid
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.artist_profile import ArtistProfile
from app.models.artwork import Artwork, ArtworkStatus
from app.models.artwork_image import ArtworkImage


class InvalidArtworkStatusError(Exception):
    pass


class ArtworkNotPublishableError(Exception):
    pass


async def list_artworks(
    db: AsyncSession, status: ArtworkStatus | str | None = None
) -> list[tuple[Artwork, str]]:
    query = (
        select(Artwork, ArtistProfile.display_name)
        .join(ArtistProfile, Artwork.artist_id == ArtistProfile.id)
    )
    if status is not None:
        query = query.where(Artwork.status == status)
    query = query.order_by(Artwork.created_at.desc())
    result = await db.execute(query)
    return [(artwork, display_name) for artwork, display_name in result.all()]


async def get_artwork_by_id(db: AsyncSession, artwork_id: uuid.UUID) -> Artwork | None:
    result = await db.execute(select(Artwork).where(Artwork.id == artwork_id))
    return result.scalars().first()


async def set_artwork_status(db: AsyncSession, artwork: Artwork, new_status: str) -> Artwork:
    valid_statuses = {s.value for s in ArtworkStatus}
    if new_status not in valid_statuses:
        raise InvalidArtworkStatusError(f"'{new_status}' is not a valid artwork status.")

    if new_status == ArtworkStatus.published.value:
        if not artwork.title or not artwork.year or not artwork.medium:
            raise ArtworkNotPublishableError(
                "Artwork needs a title, year, and medium before it can be published."
            )
        image_result = await db.execute(
            select(ArtworkImage.id).where(ArtworkImage.artwork_id == artwork.id).limit(1)
        )
        if image_result.first() is None:
            raise ArtworkNotPublishableError(
                "Artwork needs at least one image before it can be published."
            )

    artwork.status = ArtworkStatus(new_status)
    if new_status == ArtworkStatus.published.value and artwork.published_at is None:
        artwork.published_at = func.now()
    await db.commit()
    await db.refresh(artwork)
    return artwork
