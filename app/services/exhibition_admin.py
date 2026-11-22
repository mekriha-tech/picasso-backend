"""Admin create / edit / publish / delete of exhibitions (VR galleries) and their artwork
assignments. Used by the HTML admin panel; the public API only reads published rows."""
import uuid
from datetime import date
from urllib.parse import urlsplit

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.artwork import Artwork
from app.models.exhibition import Exhibition, ExhibitionArtwork, ViewerKind
from app.services.catalogue import PUBLIC_STATUSES
from app.services.slugs import generate_unique_slug, slugify

PUBLISH_NEEDS_ARTWORKS = "Add at least one artwork before publishing a first-party gallery."
VR_URL_MESSAGE = "Enter a full web address starting with https://."
MAX_SORT_ORDER = 32767


class ExhibitionValidationError(Exception):
    """Carries a {field: message} dict so the form can show each error next to its field."""

    def __init__(self, errors: dict[str, str]):
        self.errors = errors
        super().__init__("; ".join(errors.values()))


class ExhibitionNotFoundError(Exception):
    pass


def _text(raw: dict, name: str) -> str | None:
    value = (raw.get(name) or "").strip()
    return value or None


def _iso_date(raw: dict, name: str, errors: dict) -> date | None:
    value = _text(raw, name)
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        errors[name] = "Enter a date as YYYY-MM-DD."
        return None


def _has_web_host(url: str) -> bool:
    if not url.lower().startswith("https://"):  # http:// is blocked as mixed content in the iframe
        return False
    try:
        return bool(urlsplit(url).hostname)
    except ValueError:  # e.g. "https://[" (invalid IPv6 literal)
        return False


def clean_exhibition_form(raw: dict, existing: Exhibition | None = None) -> dict:
    """Validates admin form input and returns clean column values, or raises
    ExhibitionValidationError. viewer_kind must be chosen explicitly. `existing` is accepted for
    signature parity with the artwork form; every field is taken from the form."""
    errors: dict[str, str] = {}
    clean: dict = {}

    title = _text(raw, "title")
    if not title:
        errors["title"] = "Give the gallery a title."
    elif len(title) > 200:
        errors["title"] = "The title can be at most 200 characters."
    clean["title"] = title

    kind = _text(raw, "viewer_kind")
    if kind not in {k.value for k in ViewerKind}:
        errors["viewer_kind"] = "Choose how visitors will view this gallery."

    url = _text(raw, "vr_tour_url")
    if url is not None and not _has_web_host(url):
        errors["vr_tour_url"] = VR_URL_MESSAGE
    elif url is None and kind == ViewerKind.vendor_embed.value:
        errors["vr_tour_url"] = VR_URL_MESSAGE
    clean["vr_tour_url"] = url

    sort_raw = _text(raw, "sort_order")
    sort_order = 0
    if sort_raw is not None:
        if not sort_raw.isdecimal() or len(sort_raw) > 10 or int(sort_raw) > MAX_SORT_ORDER:
            errors["sort_order"] = f"Sort order must be a whole number from 0 to {MAX_SORT_ORDER}."
        else:
            sort_order = int(sort_raw)
    clean["sort_order"] = sort_order

    clean["starts_on"] = _iso_date(raw, "starts_on", errors)
    clean["ends_on"] = _iso_date(raw, "ends_on", errors)

    for name, limit in (("description", 10000), ("cover_image_url", 2000)):
        value = _text(raw, name)
        if value and len(value) > limit:
            errors[name] = f"This can be at most {limit} characters."
        clean[name] = value

    if errors:
        raise ExhibitionValidationError(errors)
    clean["viewer_kind"] = kind
    return clean


async def _get(db: AsyncSession, exhibition_id: uuid.UUID) -> Exhibition:
    exhibition = await db.get(Exhibition, exhibition_id)
    if exhibition is None:
        raise ExhibitionNotFoundError(str(exhibition_id))
    return exhibition


async def _artwork_count(db: AsyncSession, exhibition_id: uuid.UUID) -> int:
    return (
        await db.execute(
            select(func.count())
            .select_from(ExhibitionArtwork)
            .join(Artwork, Artwork.id == ExhibitionArtwork.artwork_id)
            .where(
                ExhibitionArtwork.exhibition_id == exhibition_id,
                Artwork.deleted_at.is_(None),
                Artwork.status.in_(PUBLIC_STATUSES),
            )
        )
    ).scalar_one()


async def _check_publishable(
    db: AsyncSession, exhibition_id: uuid.UUID, viewer_kind: str, field: str
) -> None:
    """A first-party gallery has nothing to show without artworks; vendor embeds only need a URL
    (guaranteed by the CHECK constraint and the form)."""
    if viewer_kind == ViewerKind.first_party.value and await _artwork_count(db, exhibition_id) == 0:
        raise ExhibitionValidationError({field: PUBLISH_NEEDS_ARTWORKS})


async def create_exhibition(db: AsyncSession, clean: dict) -> Exhibition:
    """Always creates an unpublished gallery; publishing goes through set_published."""
    base = slugify(clean["title"])
    for attempt in range(3):
        taken = set(
            (await db.execute(select(Exhibition.slug).where(Exhibition.slug.like(f"{base}%"))))
            .scalars()
            .all()
        )
        exhibition = Exhibition(
            **clean, slug=generate_unique_slug(clean["title"], taken), is_published=False
        )
        db.add(exhibition)
        try:
            await db.commit()
        except IntegrityError:  # slug raced with another create; recompute and retry
            await db.rollback()
            if attempt == 2:
                raise
            continue
        await db.refresh(exhibition)
        return exhibition
    raise RuntimeError("unreachable")


async def update_exhibition(db: AsyncSession, exhibition_id: uuid.UUID, clean: dict) -> Exhibition:
    exhibition = await _get(db, exhibition_id)
    if exhibition.is_published:
        await _check_publishable(db, exhibition.id, clean["viewer_kind"], "viewer_kind")
    for key, value in clean.items():
        setattr(exhibition, key, value)  # slug is never changed: public URLs stay stable
    await db.commit()
    await db.refresh(exhibition)
    return exhibition


async def set_published(db: AsyncSession, exhibition_id: uuid.UUID, published: bool) -> Exhibition:
    exhibition = await _get(db, exhibition_id)
    if published:
        await _check_publishable(db, exhibition.id, exhibition.viewer_kind, "published")
    exhibition.is_published = published
    await db.commit()
    await db.refresh(exhibition)
    return exhibition


async def set_artworks(
    db: AsyncSession, exhibition_id: uuid.UUID, positions: dict[uuid.UUID, int | None]
) -> None:
    """Replaces the gallery's full artwork assignment. Wall positions must be unique among the
    non-null values. A published first-party gallery must keep at least one publicly visible
    artwork (public status, not deleted)."""
    exhibition = await _get(db, exhibition_id)

    used = [p for p in positions.values() if p is not None]
    if len(used) != len(set(used)):
        raise ExhibitionValidationError({"artworks": "Each artwork needs its own wall position."})
    if positions:
        found = set(
            (
                await db.execute(
                    select(Artwork.id).where(
                        Artwork.id.in_(list(positions)), Artwork.deleted_at.is_(None)
                    )
                )
            )
            .scalars()
            .all()
        )
        if found != set(positions):
            raise ExhibitionValidationError({"artworks": "Choose artworks from the list."})
    if exhibition.is_published and exhibition.viewer_kind == ViewerKind.first_party.value:
        visible = 0
        if positions:
            visible = (
                await db.execute(
                    select(func.count())
                    .select_from(Artwork)
                    .where(
                        Artwork.id.in_(list(positions)),
                        Artwork.deleted_at.is_(None),
                        Artwork.status.in_(PUBLIC_STATUSES),
                    )
                )
            ).scalar_one()
        if visible == 0:
            raise ExhibitionValidationError({"artworks": PUBLISH_NEEDS_ARTWORKS})

    await db.execute(
        delete(ExhibitionArtwork).where(ExhibitionArtwork.exhibition_id == exhibition_id)
    )
    db.add_all(
        ExhibitionArtwork(exhibition_id=exhibition_id, artwork_id=art_id, wall_position=pos)
        for art_id, pos in positions.items()
    )
    await db.commit()


async def delete_exhibition(db: AsyncSession, exhibition_id: uuid.UUID) -> None:
    """Hard delete. Assignments are removed explicitly (not only via ON DELETE CASCADE); the
    artworks themselves are untouched."""
    exhibition = await _get(db, exhibition_id)
    await db.execute(
        delete(ExhibitionArtwork).where(ExhibitionArtwork.exhibition_id == exhibition_id)
    )
    await db.delete(exhibition)
    await db.commit()


async def get_exhibition(db: AsyncSession, exhibition_id: uuid.UUID) -> Exhibition | None:
    return await db.get(Exhibition, exhibition_id)


async def list_exhibitions(db: AsyncSession) -> list[tuple[Exhibition, int]]:
    """Every gallery (published or not) with its count of assigned artworks that are publicly visible (not draft, not deleted)."""
    counts = (
        select(ExhibitionArtwork.exhibition_id, func.count().label("n"))
        .join(Artwork, Artwork.id == ExhibitionArtwork.artwork_id)
        .where(Artwork.deleted_at.is_(None), Artwork.status.in_(PUBLIC_STATUSES))
        .group_by(ExhibitionArtwork.exhibition_id)
        .subquery()
    )
    rows = await db.execute(
        select(Exhibition, func.coalesce(counts.c.n, 0))
        .outerjoin(counts, counts.c.exhibition_id == Exhibition.id)
        .order_by(Exhibition.sort_order, Exhibition.created_at, Exhibition.id)
    )
    return [(ex, int(n)) for ex, n in rows.all()]


async def list_assignments(db: AsyncSession, exhibition_id: uuid.UUID) -> dict[uuid.UUID, int | None]:
    """artwork_id -> wall_position for the gallery's current assignment."""
    rows = await db.execute(
        select(ExhibitionArtwork.artwork_id, ExhibitionArtwork.wall_position).where(
            ExhibitionArtwork.exhibition_id == exhibition_id
        )
    )
    return {art_id: pos for art_id, pos in rows.all()}
