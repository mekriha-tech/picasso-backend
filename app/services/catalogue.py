import uuid
from datetime import datetime
from decimal import Decimal, InvalidOperation
from sqlalchemy import select, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.artist_profile import ArtistProfile
from app.models.artwork import Artwork, ArtworkStatus, ListingType
from app.models.artwork_image import ArtworkImage
from app.services.badges import compute_badge
from app.services.pagination import encode_cursor, decode_cursor

PUBLIC_STATUSES = (ArtworkStatus.published, ArtworkStatus.reserved, ArtworkStatus.sold)

SORT_COLUMNS = {
    "newest": Artwork.published_at,
    "price_asc": Artwork.price,
    "price_desc": Artwork.price,
    # No auctions table yet, so there's no real "time until close" to sort by - this falls
    # back to newest-first rather than erroring on an otherwise-valid PRD sort value.
    "ending_soon": Artwork.published_at,
}


class InvalidCursorError(Exception):
    pass


def _artwork_card_dict(artwork: Artwork, artist_slug: str, artist_display_name: str) -> dict:
    return {
        "id": str(artwork.id),
        "slug": artwork.slug,
        "title": artwork.title,
        "artist": {"slug": artist_slug, "display_name": artist_display_name},
        "primary_image_url": artwork.primary_image_url,
        "medium": artwork.medium,
        "dimensions": artwork.dimensions,
        "year": artwork.year,
        "listing_type": artwork.listing_type,
        "status": artwork.status,
        "badge": compute_badge(artwork.listing_type, artwork.status, artwork.sold),
        "price": str(artwork.price) if artwork.price is not None else None,
        "sold": artwork.sold,
        "sold_price": str(artwork.sold_price) if artwork.sold_price is not None else None,
        "auction": None,
        "in_wishlist": False,
    }


async def list_public_artworks(
    db: AsyncSession,
    *,
    listing_type: str | None = None,
    category: str | None = None,
    artist_id: uuid.UUID | None = None,
    min_price: Decimal | None = None,
    max_price: Decimal | None = None,
    q: str | None = None,
    sort: str = "newest",
    limit: int = 24,
    cursor: str | None = None,
) -> tuple[list[dict], str | None]:
    sort_col = SORT_COLUMNS.get(sort, Artwork.published_at)
    descending = sort != "price_asc"

    query = (
        select(Artwork, ArtistProfile.slug, ArtistProfile.display_name)
        .join(ArtistProfile, Artwork.artist_id == ArtistProfile.id)
        .where(Artwork.status.in_(PUBLIC_STATUSES))
    )
    if listing_type is not None:
        query = query.where(Artwork.listing_type == listing_type)
    if category is not None:
        query = query.where(Artwork.category == category)
    if artist_id is not None:
        query = query.where(Artwork.artist_id == artist_id)
    if min_price is not None:
        query = query.where(Artwork.price >= min_price)
    if max_price is not None:
        query = query.where(Artwork.price <= max_price)
    if sort in ("price_asc", "price_desc"):
        query = query.where(Artwork.price.isnot(None))
    if q:
        query = query.where(Artwork.title.ilike(f"%{q}%"))

    if cursor:
        decoded = decode_cursor(cursor)
        if decoded is None:
            raise InvalidCursorError("Invalid cursor")
        raw_value, cursor_id = decoded
        try:
            cursor_value = (
                datetime.fromisoformat(raw_value)
                if sort_col is Artwork.published_at
                else Decimal(raw_value)
            )
        except (ValueError, InvalidOperation, TypeError):
            raise InvalidCursorError("Invalid cursor")

        if descending:
            query = query.where(
                or_(sort_col < cursor_value, and_(sort_col == cursor_value, Artwork.id < cursor_id))
            )
        else:
            query = query.where(
                or_(sort_col > cursor_value, and_(sort_col == cursor_value, Artwork.id > cursor_id))
            )

    order = sort_col.desc() if descending else sort_col.asc()
    id_order = Artwork.id.desc() if descending else Artwork.id.asc()
    query = query.order_by(order, id_order).limit(limit + 1)

    rows = (await db.execute(query)).all()

    has_more = len(rows) > limit
    rows = rows[:limit]
    items = [_artwork_card_dict(artwork, slug, name) for artwork, slug, name in rows]

    next_cursor = None
    if has_more and rows:
        last_artwork = rows[-1][0]
        last_sort_value = getattr(last_artwork, sort_col.key)
        if last_sort_value is not None:
            next_cursor = encode_cursor(last_sort_value, last_artwork.id)

    return items, next_cursor


async def get_artwork_by_slug(db: AsyncSession, slug: str) -> dict | None:
    query = (
        select(Artwork, ArtistProfile.slug, ArtistProfile.display_name)
        .join(ArtistProfile, Artwork.artist_id == ArtistProfile.id)
        .where(Artwork.slug == slug)
        .where(Artwork.status.in_(PUBLIC_STATUSES))
    )
    row = (await db.execute(query)).first()
    if row is None:
        return None
    artwork, artist_slug, artist_display_name = row

    images = (
        (
            await db.execute(
                select(ArtworkImage)
                .where(ArtworkImage.artwork_id == artwork.id)
                .order_by(ArtworkImage.sort_order)
            )
        )
        .scalars()
        .all()
    )

    card = _artwork_card_dict(artwork, artist_slug, artist_display_name)
    card.update(
        {
            "description": artwork.description,
            "width_cm": str(artwork.width_cm) if artwork.width_cm is not None else None,
            "height_cm": str(artwork.height_cm) if artwork.height_cm is not None else None,
            "category": artwork.category,
            "view_count": artwork.view_count,
            "images": [
                {
                    "url": img.url,
                    "alt_text": img.alt_text,
                    "sort_order": img.sort_order,
                    "is_primary": img.is_primary,
                }
                for img in images
            ],
        }
    )
    return card


async def get_featured_artworks(db: AsyncSession) -> dict:
    result: dict[str, list[dict]] = {}
    for lt in (ListingType.sale, ListingType.auction, ListingType.display):
        query = (
            select(Artwork, ArtistProfile.slug, ArtistProfile.display_name)
            .join(ArtistProfile, Artwork.artist_id == ArtistProfile.id)
            .where(Artwork.status.in_(PUBLIC_STATUSES))
            .where(Artwork.listing_type == lt)
            .order_by(Artwork.published_at.desc())
            .limit(3)
        )
        rows = (await db.execute(query)).all()
        result[lt.value] = [_artwork_card_dict(a, s, n) for a, s, n in rows]
    return result


def _artist_card_dict(artist: ArtistProfile) -> dict:
    return {
        "slug": artist.slug,
        "display_name": artist.display_name,
        "primary_medium": artist.primary_medium,
        "cover_image_url": artist.cover_image_url,
        "is_featured": artist.is_featured,
    }


async def list_artists(
    db: AsyncSession, *, limit: int = 24, cursor: str | None = None
) -> tuple[list[dict], str | None]:
    has_published_artwork = (
        select(Artwork.id)
        .where(Artwork.artist_id == ArtistProfile.id)
        .where(Artwork.status.in_(PUBLIC_STATUSES))
        .exists()
    )
    query = select(ArtistProfile).where(has_published_artwork)

    if cursor:
        decoded = decode_cursor(cursor)
        if decoded is None:
            raise InvalidCursorError("Invalid cursor")
        raw_value, cursor_id = decoded
        try:
            cursor_value = datetime.fromisoformat(raw_value)
        except (ValueError, TypeError):
            raise InvalidCursorError("Invalid cursor")
        query = query.where(
            or_(
                ArtistProfile.approved_at < cursor_value,
                and_(ArtistProfile.approved_at == cursor_value, ArtistProfile.id < cursor_id),
            )
        )

    query = query.order_by(ArtistProfile.approved_at.desc(), ArtistProfile.id.desc()).limit(limit + 1)
    rows = (await db.execute(query)).scalars().all()

    has_more = len(rows) > limit
    rows = rows[:limit]
    items = [_artist_card_dict(a) for a in rows]

    next_cursor = None
    if has_more and rows:
        last = rows[-1]
        next_cursor = encode_cursor(last.approved_at, last.id)

    return items, next_cursor


async def get_artist_by_slug(db: AsyncSession, slug: str) -> dict | None:
    artist = (
        await db.execute(select(ArtistProfile).where(ArtistProfile.slug == slug))
    ).scalars().first()
    if artist is None:
        return None

    works_query = (
        select(Artwork, ArtistProfile.slug, ArtistProfile.display_name)
        .join(ArtistProfile, Artwork.artist_id == ArtistProfile.id)
        .where(Artwork.artist_id == artist.id)
        .where(Artwork.status.in_(PUBLIC_STATUSES))
        .order_by(Artwork.published_at.desc())
    )
    rows = (await db.execute(works_query)).all()
    works = [_artwork_card_dict(a, s, n) for a, s, n in rows]

    card = _artist_card_dict(artist)
    card.update(
        {
            "statement": artist.statement,
            "years_practising": artist.years_practising,
            "website_url": artist.website_url,
            "instagram": artist.instagram,
            "approved_at": artist.approved_at,
            "works": works,
        }
    )
    return card
