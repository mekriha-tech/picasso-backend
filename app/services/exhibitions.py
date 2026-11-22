import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.artist_profile import ArtistProfile
from app.models.artwork import Artwork
from app.models.artwork_image import ArtworkImage
from app.models.exhibition import Exhibition, ExhibitionArtwork, ViewerKind
from app.services.catalogue import PUBLIC_STATUSES, artwork_card_dict


def _card(ex: Exhibition) -> dict:
    return {
        "id": str(ex.id),
        "title": ex.title,
        "slug": ex.slug,
        "cover_image_url": ex.cover_image_url,
        "viewer_kind": ex.viewer_kind,
    }


async def list_published_exhibitions(db: AsyncSession) -> list[dict]:
    rows = await db.execute(
        select(Exhibition)
        .where(Exhibition.is_published.is_(True))
        .order_by(Exhibition.sort_order, Exhibition.created_at, Exhibition.id)
    )
    return [_card(ex) for ex in rows.scalars().all()]


def _dim(value) -> str | None:
    return str(value) if value is not None else None


async def _exhibition_artworks(
    db: AsyncSession, exhibition_id: uuid.UUID, only_ids: set[uuid.UUID] | None
) -> list[dict]:
    query = (
        select(
            Artwork, ArtistProfile.slug, ArtistProfile.display_name,
            ExhibitionArtwork.wall_position, ArtworkImage,
        )
        .join(ExhibitionArtwork, ExhibitionArtwork.artwork_id == Artwork.id)
        .join(ArtistProfile, Artwork.artist_id == ArtistProfile.id)
        .outerjoin(
            ArtworkImage,
            (ArtworkImage.artwork_id == Artwork.id) & ArtworkImage.is_primary.is_(True),
        )
        .where(ExhibitionArtwork.exhibition_id == exhibition_id)
        .where(Artwork.status.in_(PUBLIC_STATUSES))
        .order_by(ExhibitionArtwork.wall_position.asc().nullslast(), Artwork.id)
    )
    if only_ids is not None:
        query = query.where(Artwork.id.in_(list(only_ids)))
    items = []
    for artwork, artist_slug, artist_name, wall_position, image in (await db.execute(query)).all():
        card = artwork_card_dict(artwork, artist_slug, artist_name)
        texture = None
        if image is not None:
            texture = image.preview_url or image.url
        card.update(
            {
                "wall_position": wall_position,
                "width_cm": _dim(artwork.width_cm),
                "height_cm": _dim(artwork.height_cm),
                "texture_url": texture or artwork.primary_image_url,
                "image_width_px": image.width_px if image is not None else None,
                "image_height_px": image.height_px if image is not None else None,
            }
        )
        items.append(card)
    return items


async def get_published_exhibition(
    db: AsyncSession, slug: str, only_ids: set[uuid.UUID] | None = None
) -> dict | None:
    ex = (
        await db.execute(
            select(Exhibition).where(Exhibition.slug == slug, Exhibition.is_published.is_(True))
        )
    ).scalar_one_or_none()
    if ex is None:
        return None
    detail = _card(ex)
    detail.update({"description": ex.description, "starts_on": ex.starts_on, "ends_on": ex.ends_on})
    if ex.viewer_kind == ViewerKind.vendor_embed.value:
        detail["vr_tour_url"] = ex.vr_tour_url
    else:
        detail["artworks"] = await _exhibition_artworks(db, ex.id, only_ids)
    return detail
