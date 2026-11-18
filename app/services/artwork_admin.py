"""Admin create / edit / delete of artworks (used by the HTML admin panel).

Status changes stay in app.services.artworks.set_artwork_status; images in artwork_images.
"""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.artist_profile import ArtistProfile
from app.models.artwork import Artwork, ArtworkStatus, ListingType
from app.services.artworks import PUBLIC_ARTWORK_STATUSES
from app.services.slugs import generate_unique_slug, slugify

MAX_PRICE = Decimal("9999999999.99")
MAX_DIMENSION_CM = Decimal("999999.99")

# Same copy the publish check in set_artwork_status uses.
PUBLISH_FIELDS_MESSAGE = "Artwork needs a title, year, and medium before it can be published."


class ArtworkValidationError(Exception):
    """Carries a {field: message} dict so the form can show each error next to its field."""

    def __init__(self, errors: dict[str, str]):
        self.errors = errors
        super().__init__("; ".join(errors.values()))


class ArtworkNotDeletableError(Exception):
    pass


def _text(raw: dict, name: str) -> str | None:
    value = (raw.get(name) or "").strip()
    return value or None


def _decimal(raw: dict, name: str, errors: dict, label: str, max_value: Decimal) -> Decimal | None:
    value = _text(raw, name)
    if value is None:
        return None
    try:
        number = Decimal(value)
    except InvalidOperation:
        errors[name] = f"{label} must be a number."
        return None
    if not number.is_finite() or number <= 0 or number > max_value:
        errors[name] = f"{label} must be greater than 0."
        return None
    if number != number.quantize(Decimal("0.01")):
        errors[name] = f"{label} can have at most 2 decimal places."
        return None
    return number.quantize(Decimal("0.01"))


async def clean_artwork_form(db: AsyncSession, raw: dict, existing: Artwork | None = None) -> dict:
    """Validates admin form input and returns clean column values, or raises
    ArtworkValidationError. Enforces the sale/display price rules (PRD rule 8) and, for artworks
    already published/reserved/sold, the publish requirements (rule 10) so an edit cannot strip
    them."""
    errors: dict[str, str] = {}
    clean: dict = {}

    artist_raw = _text(raw, "artist_id")
    if existing is not None and not artist_raw:
        clean["artist_id"] = existing.artist_id
    else:
        try:
            artist_id = uuid.UUID(artist_raw or "")
        except ValueError:
            artist_id = None
        found = None
        if artist_id is not None:
            found = (
                await db.execute(select(ArtistProfile.id).where(ArtistProfile.id == artist_id))
            ).first()
        if found is None:
            errors["artist_id"] = "Choose an artist."
        else:
            clean["artist_id"] = artist_id

    title = _text(raw, "title")
    if not title:
        errors["title"] = "Give the artwork a title."
    elif len(title) > 200:
        errors["title"] = "The title can be at most 200 characters."
    clean["title"] = title

    public = existing is not None and existing.status.value in PUBLIC_ARTWORK_STATUSES

    year = None
    year_raw = _text(raw, "year")
    if year_raw is not None:
        max_year = date.today().year + 1
        if not year_raw.isdigit() or not 1000 <= int(year_raw) <= max_year:
            errors["year"] = f"Year must be between 1000 and {max_year}."
        else:
            year = int(year_raw)
    elif public:
        errors["year"] = PUBLISH_FIELDS_MESSAGE
    clean["year"] = year

    medium = _text(raw, "medium")
    if medium and len(medium) > 200:
        errors["medium"] = "The medium can be at most 200 characters."
    elif not medium and public:
        errors["medium"] = PUBLISH_FIELDS_MESSAGE
    clean["medium"] = medium

    for name, limit in (("dimensions", 200), ("category", 100), ("description", 10000)):
        value = _text(raw, name)
        if value and len(value) > limit:
            errors[name] = f"This can be at most {limit} characters."
        clean[name] = value

    clean["width_cm"] = _decimal(raw, "width_cm", errors, "Width", MAX_DIMENSION_CM)
    clean["height_cm"] = _decimal(raw, "height_cm", errors, "Height", MAX_DIMENSION_CM)

    listing = _text(raw, "listing_type") or (existing.listing_type.value if existing else "")
    is_existing_auction = existing is not None and existing.listing_type == ListingType.auction
    if listing not in {t.value for t in ListingType}:
        errors["listing_type"] = "Choose a listing type."
    elif listing == ListingType.auction.value and not is_existing_auction:
        # Rule 8: an auction listing needs an auction row, and auctions are not built yet.
        errors["listing_type"] = "Auction listings can't be created here yet."

    price = _decimal(raw, "price", errors, "Price", MAX_PRICE)
    if "price" not in errors:
        if listing == ListingType.sale.value and price is None:
            errors["price"] = "A price is required for items listed for sale."
        elif listing == ListingType.display.value and price is not None:
            errors["price"] = "Items on display only can't have a price."
    if listing == ListingType.auction.value and is_existing_auction:
        price = existing.price  # auction pricing is not edited here
    clean["price"] = price

    if existing is not None and (existing.sold or existing.status == ArtworkStatus.sold):
        old_price = Decimal(existing.price) if existing.price is not None else None
        if "listing_type" not in errors and "price" not in errors and (
            listing != existing.listing_type.value or price != old_price
        ):
            errors["listing_type"] = "Sold artworks can't change listing type or price."

    if errors:
        raise ArtworkValidationError(errors)
    clean["listing_type"] = ListingType(listing)
    return clean


async def create_artwork(db: AsyncSession, raw: dict) -> Artwork:
    """Creates a draft. Publishing goes through set_artwork_status, which enforces rule 10."""
    clean = await clean_artwork_form(db, raw)
    base = slugify(clean["title"])
    for attempt in range(3):
        taken = set(
            (await db.execute(select(Artwork.slug).where(Artwork.slug.like(f"{base}%"))))
            .scalars()
            .all()
        )
        artwork = Artwork(
            **clean, slug=generate_unique_slug(clean["title"], taken), status=ArtworkStatus.draft
        )
        db.add(artwork)
        try:
            await db.commit()
        except IntegrityError:  # slug raced with another create; recompute and retry
            await db.rollback()
            if attempt == 2:
                raise
            continue
        await db.refresh(artwork)
        return artwork
    raise RuntimeError("unreachable")


async def update_artwork(db: AsyncSession, artwork: Artwork, raw: dict) -> Artwork:
    clean = await clean_artwork_form(db, raw, existing=artwork)
    for key, value in clean.items():
        setattr(artwork, key, value)  # slug is never changed: public URLs stay stable
    await db.commit()
    await db.refresh(artwork)
    return artwork


async def delete_artwork(db: AsyncSession, artwork: Artwork) -> None:
    """Soft delete (PRD 4.7). Sold and reserved artworks are part of order history, so they
    stay. Images are kept in storage so the artwork can be restored. When auctions exist, an
    artwork whose auction has bids must be refused here too (rule 9)."""
    if artwork.sold or artwork.status == ArtworkStatus.sold:
        raise ArtworkNotDeletableError("Sold artworks can't be deleted.")
    if artwork.status == ArtworkStatus.reserved:
        raise ArtworkNotDeletableError(
            "Reserved artworks can't be deleted until the reservation is released."
        )
    artwork.deleted_at = datetime.now(timezone.utc)
    artwork.status = ArtworkStatus.removed
    await db.commit()


async def list_artist_choices(db: AsyncSession) -> list[tuple[uuid.UUID, str]]:
    result = await db.execute(
        select(ArtistProfile.id, ArtistProfile.display_name).order_by(ArtistProfile.display_name)
    )
    return [(i, n) for i, n in result.all()]
