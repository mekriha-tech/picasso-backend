# Public Catalogue Endpoints Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the frontend real data to render on the homepage/explore/artist pages: a filtered, sorted, cursor-paginated artworks list, artwork detail, a featured-artworks endpoint, and an artists list/detail — plus the one small change needed to get any artwork into a publishable state (PRD rule 10 validation on the existing admin status-change endpoint).

**Architecture:** Two new pure/testable helpers (cursor encode/decode, badge derivation) feed a new `app/services/catalogue.py` that both new public routes (`app/api/v1/catalogue.py`) call. No new tables — `artworks`, `artwork_images`, `artist_profiles` already exist from the admin-panel work.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 async, Pydantic v2, pytest — same stack as every prior piece of work in this repo.

**Spec:** `docs/superpowers/specs/2026-09-05-public-catalogue-design.md`

## Global Constraints

- Money fields (`price`, `sold_price`, `width_cm`, `height_cm`) are serialized as **strings**, not
  JSON numbers — the DB stores them as `Numeric`/`Decimal`, and converting to a bare JSON number
  risks float precision loss in JS clients. Every response dict in this plan does
  `str(value) if value is not None else None` for these fields, never passes the raw `Decimal`
  through.
- Every new endpoint filters to `Artwork.status.in_((ArtworkStatus.published,
  ArtworkStatus.reserved, ArtworkStatus.sold))` — `draft`/`unlisted`/`removed` must never appear
  in a public response, no exceptions, no matter what other filters are applied.
- No local Postgres is reachable from this dev environment. Verify with `ast.parse` for every
  Python file; real pytest unit tests for the two pure helpers (cursor, badge); DB-touching
  service/route code is verified via a live smoke test against the Railway deployment as the
  final task, matching every prior batch of work in this repo's history.
- This repo's "no relationships" convention continues: no SQLAlchemy `relationship()` anywhere;
  every join is an explicit `select()` in the service layer, and response dicts are built by hand
  (matching the existing `_to_out`/`_to_admin_out` pattern in `app/api/v1/artist_applications.py`
  / `app/api/v1/admin.py`), not via Pydantic `from_attributes` on a joined row.
- Business rules live in the service layer, not route handlers (CLAUDE.md) — routes only
  translate HTTP ↔ service calls.

---

### Task 1: Cursor pagination helper (TDD)

**Files:**
- Create: `app/services/pagination.py`
- Test: `tests/test_pagination.py`

**Interfaces:**
- Produces: `encode_cursor(sort_value: Any, row_id: uuid.UUID) -> str`,
  `decode_cursor(cursor: str) -> tuple[str, uuid.UUID] | None` (returns `None` on any malformed
  input — bad base64, bad JSON, missing keys, invalid UUID). The decoded sort value always comes
  back as the **string** that was encoded — converting it to the right type (datetime vs Decimal)
  is the caller's job, since this helper has no idea which column is being paginated. Consumed by
  Task 5 (artworks) and Task 6 (artists).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_pagination.py
import uuid
from datetime import datetime, timezone

from app.services.pagination import encode_cursor, decode_cursor


def test_round_trip_datetime_value():
    row_id = uuid.uuid4()
    dt = datetime(2026, 1, 15, 10, 30, tzinfo=timezone.utc)
    cursor = encode_cursor(dt, row_id)
    decoded_value, decoded_id = decode_cursor(cursor)
    assert decoded_value == dt.isoformat()
    assert decoded_id == row_id


def test_round_trip_numeric_value():
    row_id = uuid.uuid4()
    cursor = encode_cursor("199.50", row_id)
    decoded_value, decoded_id = decode_cursor(cursor)
    assert decoded_value == "199.50"
    assert decoded_id == row_id


def test_decode_rejects_garbage_base64():
    assert decode_cursor("not-valid-base64!!!") is None


def test_decode_rejects_valid_base64_bad_json():
    import base64
    garbage = base64.urlsafe_b64encode(b"not json").decode()
    assert decode_cursor(garbage) is None


def test_decode_rejects_missing_id_field():
    import base64
    import json
    payload = base64.urlsafe_b64encode(json.dumps({"v": "x"}).encode()).decode()
    assert decode_cursor(payload) is None


def test_decode_rejects_invalid_uuid():
    import base64
    import json
    payload = base64.urlsafe_b64encode(
        json.dumps({"v": "x", "id": "not-a-uuid"}).encode()
    ).decode()
    assert decode_cursor(payload) is None
```

- [ ] **Step 2: Run it, confirm it fails**

Run: `python -m pytest tests/test_pagination.py -v`
Expected: `ModuleNotFoundError: No module named 'app.services.pagination'`

- [ ] **Step 3: Implement `app/services/pagination.py`**

```python
import base64
import json
import uuid
from datetime import datetime
from typing import Any


def encode_cursor(sort_value: Any, row_id: uuid.UUID) -> str:
    """Encodes an opaque pagination cursor from a (sort column value, row id) pair.

    The caller passes whatever value the active sort column holds for the last row of the
    current page; on the next request, decode_cursor() gives that value back (as a string) so
    the caller can build a keyset WHERE clause instead of an OFFSET.
    """
    v = sort_value.isoformat() if isinstance(sort_value, datetime) else str(sort_value)
    payload = json.dumps({"v": v, "id": str(row_id)})
    return base64.urlsafe_b64encode(payload.encode()).decode()


def decode_cursor(cursor: str) -> tuple[str, uuid.UUID] | None:
    """Returns (sort_value_as_string, row_id), or None if the cursor is malformed in any way."""
    try:
        payload = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
        return payload["v"], uuid.UUID(payload["id"])
    except Exception:
        return None
```

- [ ] **Step 4: Run it, confirm it passes**

Run: `python -m pytest tests/test_pagination.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add app/services/pagination.py tests/test_pagination.py
git commit -m "feat: add opaque cursor-pagination encode/decode helper"
```

---

### Task 2: Badge derivation helper (TDD)

**Files:**
- Create: `app/services/badges.py`
- Test: `tests/test_badges.py`

**Interfaces:**
- Produces: `compute_badge(listing_type: str, status: str, sold: bool) -> str` — consumed by
  Task 5's `_artwork_card_dict`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_badges.py
from app.services.badges import compute_badge


def test_auction_is_live_auction():
    assert compute_badge("auction", "published", False) == "Live Auction"


def test_sale_is_for_sale():
    assert compute_badge("sale", "published", False) == "For Sale"


def test_display_sold_is_sold():
    assert compute_badge("display", "sold", True) == "Sold"


def test_display_not_sold_is_on_display():
    assert compute_badge("display", "published", False) == "On Display"
```

- [ ] **Step 2: Run it, confirm it fails**

Run: `python -m pytest tests/test_badges.py -v`
Expected: `ModuleNotFoundError: No module named 'app.services.badges'`

- [ ] **Step 3: Implement `app/services/badges.py`**

```python
def compute_badge(listing_type: str, status: str, sold: bool) -> str:
    """PRD §5's badge mapping. listing_type takes priority over sold - by PRD rule 20, a sold
    artwork always flips to listing_type='display' anyway, so a "sale"+sold combination
    shouldn't occur in practice, but this function is deterministic either way."""
    if listing_type == "auction":
        return "Live Auction"
    if listing_type == "sale":
        return "For Sale"
    if sold:
        return "Sold"
    return "On Display"
```

- [ ] **Step 4: Run it, confirm it passes**

Run: `python -m pytest tests/test_badges.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add app/services/badges.py tests/test_badges.py
git commit -m "feat: add PRD-§5 badge derivation helper"
```

---

### Task 3: Publish validation (PRD rule 10)

**Files:**
- Modify: `app/services/artworks.py`
- Modify: `app/api/v1/admin.py`
- Modify: `app/admin_panel/artwork_routes.py`

**Interfaces:**
- Consumes: `ArtworkImage` model (`app/models/artwork_image.py`, already exists)
- Produces: `ArtworkNotPublishableError` (new exception) — consumed by the JSON admin route
  (→ `409`) and the HTML admin panel route (→ `?error=` redirect), same pattern already used for
  the existing `InvalidArtworkStatusError` in both files.

This is the only way anything becomes visible through the new catalogue endpoints (Tasks 5-8) —
every artwork today is `status='draft'`.

- [ ] **Step 1: Add the import and exception to `app/services/artworks.py`**

Add to the imports at the top:
```python
from app.models.artwork_image import ArtworkImage
```

Add below `InvalidArtworkStatusError`:
```python
class ArtworkNotPublishableError(Exception):
    pass
```

- [ ] **Step 2: Add the rule-10 check inside `set_artwork_status`**

Replace the existing function body with:
```python
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
    await db.commit()
    await db.refresh(artwork)
    return artwork
```

- [ ] **Step 3: Map the new exception to `409` in `app/api/v1/admin.py`**

In `update_artwork_status_route`, change:
```python
    try:
        artwork = await artworks_service.set_artwork_status(db, artwork, payload.status)
    except artworks_service.InvalidArtworkStatusError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
```
to:
```python
    try:
        artwork = await artworks_service.set_artwork_status(db, artwork, payload.status)
    except artworks_service.InvalidArtworkStatusError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except artworks_service.ArtworkNotPublishableError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
```

- [ ] **Step 4: Map the new exception to the `?error=` redirect in `app/admin_panel/artwork_routes.py`**

Change:
```python
        try:
            await artworks_service.set_artwork_status(db, artwork, new_status)
        except artworks_service.InvalidArtworkStatusError as exc:
            return RedirectResponse(
                url=f"/admin/artworks?error={quote(str(exc))}", status_code=303
            )
```
to:
```python
        try:
            await artworks_service.set_artwork_status(db, artwork, new_status)
        except (
            artworks_service.InvalidArtworkStatusError,
            artworks_service.ArtworkNotPublishableError,
        ) as exc:
            return RedirectResponse(
                url=f"/admin/artworks?error={quote(str(exc))}", status_code=303
            )
```

- [ ] **Step 5: Verify with `ast.parse`** on all three files

- [ ] **Step 6: Commit**

```bash
git add app/services/artworks.py app/api/v1/admin.py app/admin_panel/artwork_routes.py
git commit -m "feat: enforce PRD rule 10 before an artwork can be published"
```

---

### Task 4: Catalogue Pydantic schemas

**Files:**
- Create: `app/schemas/catalogue.py`

**Interfaces:**
- Produces: `ArtistEmbed`, `ArtworkImageOut`, `ArtworkCardOut`, `ArtworkDetailOut`,
  `PaginatedArtworksOut`, `FeaturedArtworksOut`, `ArtistCardOut`, `ArtistDetailOut`,
  `PaginatedArtistsOut` — consumed by Task 7's routes as `response_model`s.

- [ ] **Step 1: Create `app/schemas/catalogue.py`**

```python
from datetime import datetime
from pydantic import BaseModel


class ArtistEmbed(BaseModel):
    slug: str
    display_name: str


class ArtworkImageOut(BaseModel):
    url: str
    alt_text: str | None
    sort_order: int
    is_primary: bool


class ArtworkCardOut(BaseModel):
    id: str
    slug: str
    title: str
    artist: ArtistEmbed
    primary_image_url: str | None
    medium: str | None
    dimensions: str | None
    year: int | None
    listing_type: str
    status: str
    badge: str
    price: str | None
    sold: bool
    sold_price: str | None
    auction: None
    in_wishlist: bool


class ArtworkDetailOut(ArtworkCardOut):
    description: str | None
    width_cm: str | None
    height_cm: str | None
    category: str | None
    view_count: int
    images: list[ArtworkImageOut]


class PaginatedArtworksOut(BaseModel):
    items: list[ArtworkCardOut]
    next_cursor: str | None


class FeaturedArtworksOut(BaseModel):
    sale: list[ArtworkCardOut]
    auction: list[ArtworkCardOut]
    display: list[ArtworkCardOut]


class ArtistCardOut(BaseModel):
    slug: str
    display_name: str
    primary_medium: str
    cover_image_url: str | None
    is_featured: bool


class ArtistDetailOut(ArtistCardOut):
    statement: str | None
    years_practising: int | None
    website_url: str | None
    instagram: str | None
    approved_at: datetime
    works: list[ArtworkCardOut]


class PaginatedArtistsOut(BaseModel):
    items: list[ArtistCardOut]
    next_cursor: str | None
```

Note: `id` is typed `str`, not `uuid.UUID` — the service layer (Tasks 5-6) builds every response
as a plain dict (this repo's established convention, matching `_to_out` in
`app/api/v1/artist_applications.py`), and `artwork.id` there is a real `uuid.UUID` object;
Pydantic coerces a `UUID` into a `str` field fine on validation, so this is just a documentation
choice to make clear the wire format is a string, not a design requirement enforced elsewhere.

- [ ] **Step 2: Verify with `ast.parse`**

- [ ] **Step 3: Commit**

```bash
git add app/schemas/catalogue.py
git commit -m "feat: add Pydantic schemas for the public catalogue endpoints"
```

---

### Task 5: Catalogue service — artworks (list, detail, featured)

**Files:**
- Create: `app/services/catalogue.py`

**Interfaces:**
- Consumes: `encode_cursor`/`decode_cursor` (Task 1), `compute_badge` (Task 2), `Artwork`/
  `ArtworkStatus`/`ListingType` (`app/models/artwork.py`), `ArtworkImage`
  (`app/models/artwork_image.py`), `ArtistProfile` (`app/models/artist_profile.py`)
- Produces: `InvalidCursorError` (exception), `PUBLIC_STATUSES` (tuple constant, also used by
  Task 6), `list_public_artworks(db, *, listing_type=None, category=None, artist_id=None,
  min_price=None, max_price=None, q=None, sort="newest", limit=24, cursor=None) -> tuple[list[dict],
  str | None]`, `get_artwork_by_slug(db, slug) -> dict | None`, `get_featured_artworks(db) ->
  dict` — all consumed by Task 7's routes.

- [ ] **Step 1: Create `app/services/catalogue.py`**

```python
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
        except (ValueError, InvalidOperation):
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
```

- [ ] **Step 2: Verify with `ast.parse`**

- [ ] **Step 3: Commit**

```bash
git add app/services/catalogue.py
git commit -m "feat: add public artwork catalogue service (list/detail/featured)"
```

---

### Task 6: Catalogue service — artists (list, detail)

**Files:**
- Modify: `app/services/catalogue.py`

**Interfaces:**
- Consumes: `PUBLIC_STATUSES`, `InvalidCursorError`, `_artwork_card_dict` (Task 5, same file)
- Produces: `list_artists(db, *, limit=24, cursor=None) -> tuple[list[dict], str | None]`,
  `get_artist_by_slug(db, slug) -> dict | None` — consumed by Task 7's routes.

- [ ] **Step 1: Append to `app/services/catalogue.py`**

```python
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
        except ValueError:
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
```

**Note:** artists with zero published artworks never appear in `list_artists` (the `EXISTS`
filter) — this is a deliberate default (no reason to show an empty directory entry), documented
in the design spec. `get_artist_by_slug` has no such filter — a direct link to an approved
artist's profile still resolves even before they have anything published, just with an empty
`works` list.

- [ ] **Step 2: Verify with `ast.parse`**

- [ ] **Step 3: Commit**

```bash
git add app/services/catalogue.py
git commit -m "feat: add public artist directory service (list/detail)"
```

---

### Task 7: Catalogue API routes

**Files:**
- Create: `app/api/v1/catalogue.py`
- Modify: `app/main.py`

**Interfaces:**
- Consumes: everything from Tasks 4, 5, 6

- [ ] **Step 1: Create `app/api/v1/catalogue.py`**

```python
import uuid
from decimal import Decimal, InvalidOperation
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.catalogue import (
    ArtistDetailOut,
    ArtworkDetailOut,
    FeaturedArtworksOut,
    PaginatedArtistsOut,
    PaginatedArtworksOut,
)
from app.services import catalogue as catalogue_service
from app.services.catalogue import InvalidCursorError

router = APIRouter()


@router.get("/artworks", response_model=PaginatedArtworksOut)
async def list_artworks_route(
    listing_type: str | None = Query(default=None),
    category: str | None = Query(default=None),
    artist_id: uuid.UUID | None = Query(default=None),
    min_price: str | None = Query(default=None),
    max_price: str | None = Query(default=None),
    q: str | None = Query(default=None),
    sort: str = Query(default="newest"),
    limit: int = Query(default=24, ge=1, le=100),
    cursor: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    try:
        min_price_dec = Decimal(min_price) if min_price is not None else None
        max_price_dec = Decimal(max_price) if max_price is not None else None
    except InvalidOperation:
        raise HTTPException(status_code=400, detail="min_price/max_price must be numeric")

    try:
        items, next_cursor = await catalogue_service.list_public_artworks(
            db,
            listing_type=listing_type,
            category=category,
            artist_id=artist_id,
            min_price=min_price_dec,
            max_price=max_price_dec,
            q=q,
            sort=sort,
            limit=limit,
            cursor=cursor,
        )
    except InvalidCursorError:
        raise HTTPException(status_code=400, detail="Invalid cursor")

    return {"items": items, "next_cursor": next_cursor}


@router.get("/artworks/featured", response_model=FeaturedArtworksOut)
async def featured_artworks_route(db: AsyncSession = Depends(get_db)):
    return await catalogue_service.get_featured_artworks(db)


@router.get("/artworks/{slug}", response_model=ArtworkDetailOut)
async def get_artwork_route(slug: str, db: AsyncSession = Depends(get_db)):
    artwork = await catalogue_service.get_artwork_by_slug(db, slug)
    if artwork is None:
        raise HTTPException(status_code=404, detail="Artwork not found")
    return artwork


@router.get("/artists", response_model=PaginatedArtistsOut)
async def list_artists_route(
    limit: int = Query(default=24, ge=1, le=100),
    cursor: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    try:
        items, next_cursor = await catalogue_service.list_artists(db, limit=limit, cursor=cursor)
    except InvalidCursorError:
        raise HTTPException(status_code=400, detail="Invalid cursor")
    return {"items": items, "next_cursor": next_cursor}


@router.get("/artists/{slug}", response_model=ArtistDetailOut)
async def get_artist_route(slug: str, db: AsyncSession = Depends(get_db)):
    artist = await catalogue_service.get_artist_by_slug(db, slug)
    if artist is None:
        raise HTTPException(status_code=404, detail="Artist not found")
    return artist
```

**Route order matters:** `/artworks/featured` is registered before `/artworks/{slug}` — FastAPI
matches routes in registration order, and a request to `/artworks/featured` would otherwise be
captured by the `{slug}` path parameter first. Keep this order; do not alphabetize or reorder it.

- [ ] **Step 2: Wire the router into `app/main.py`**

Update the import line:
```python
from app.api.v1 import auth, artist_applications, admin, catalogue
```
Add the include (no auth dependency — these are public per PRD §4.2):
```python
app.include_router(catalogue.router, prefix=settings.API_V1_PREFIX, tags=["Catalogue"])
```

- [ ] **Step 3: Verify with `ast.parse`** on both files

- [ ] **Step 4: Commit**

```bash
git add app/api/v1/catalogue.py app/main.py
git commit -m "feat: add public catalogue API routes"
```

---

### Task 8: Final wiring check, live smoke test, and docs update

**Files:**
- Modify: `docs/API_REFERENCE.md`

**Interfaces:**
- Consumes: everything from Tasks 1-7, deployed.

- [ ] **Step 1: Full local sanity pass before pushing**

Run: `python -c "import ast, pathlib; [ast.parse(p.read_text()) for p in pathlib.Path('app').rglob('*.py')]; print('OK')"`
Expected: `OK`

Run: `python -m pytest tests/ -v`
Expected: all pure-helper tests (Task 1's 6 + Task 2's 4 + every earlier task's tests) still pass.

- [ ] **Step 2: Push, open the PR, merge, confirm redeploy**

Standard flow used throughout this repo's history: push the branch, open a PR against `main`,
wait for it to merge, then confirm `railway status` shows the service back to `● Online` with a
new deployment ID. **Double-check the deploy actually succeeds this time** — the previous batch
of work (admin panel) hit a real production-down bug (a migration double-creating a Postgres
enum type) that only surfaced live, never in any offline check. This task adds no migrations, so
that specific failure mode doesn't apply, but don't skip watching the deploy logs regardless.

- [ ] **Step 3: Live smoke test**

Against the deployed URL, using the existing admin credentials and an already-approved artist
from prior smoke testing (or a fresh register → apply → submit → admin-approve cycle if none is
handy):

1. `GET /api/v1/artworks` → expect `200`, `{"items": [], "next_cursor": null}` (nothing published
   yet).
2. As admin, `PATCH /api/v1/admin/artworks/{id}` with `{"status": "published"}` on a draft
   artwork that's missing its `medium` (or `year`) → expect `409` (rule-10 guard).
3. `PUT`/fix the artwork's missing field via direct DB update if needed (no Studio endpoint
   exists to edit it yet — this is a known, documented gap), or pick a draft artwork that already
   has title/year/medium/image filled in (the ones auto-created on approval always do, per rule
   5) → `PATCH .../publish` → expect `200`, `status: "published"`.
4. `GET /api/v1/artworks` again → expect the just-published artwork to appear, with `badge: "On
   Display"`, `price: null`, `auction: null`, `in_wishlist: false`.
5. `GET /api/v1/artworks/{slug}` (the slug from step 4) → expect `200` with the full `images`
   array populated.
6. `GET /api/v1/artworks/featured` → expect the published artwork under `"display"`, and `"sale"`/
   `"auction"` as empty arrays.
7. `GET /api/v1/artists` → expect the artist who owns the published artwork to appear now (they
   didn't before any of their artworks were published).
8. `GET /api/v1/artists/{slug}` → expect their profile with the published artwork inside `works`.
9. Publish a second artwork so there are 2+ published rows, then call `GET /api/v1/artworks?
   limit=1` → expect `next_cursor` to be non-null; call again with `?limit=1&cursor=<that value>`
   → expect the second artwork, and `next_cursor: null` this time (no more pages).
10. `GET /api/v1/artworks?cursor=garbage` → expect a clean `400`, not a 500.
11. `GET /api/v1/artworks?sort=price_asc` → expect `200` (exercises the price sort path even
    though every artwork today has `price: null` — confirms it doesn't crash on all-NULL sort
    data).

- [ ] **Step 4: Update `docs/API_REFERENCE.md`**

Move the 5 new endpoints from "🚧 not built" into the "✅ implemented" section with full
request/response detail (mirroring the style already used for every other endpoint in that
file), document the `ArtworkNotPublishableError` `409` on the admin publish action, and update
§4's priority build plan (Phase 2 item 9 goes from 🚧 to ✅). Note the known limitations plainly,
the same way earlier sections of this doc already do: `ending_soon` behaves like `newest` (no
auctions table), `price_asc`/`price_desc` have no real data to sort yet (every artwork has
`price: null` until a `sale`-listing-type creation flow exists, which is Studio work, still
un-built), and there's no way to fix a draft artwork's missing fields yet short of a direct DB
edit (no Studio edit endpoint exists) if it fails the rule-10 publish check.

- [ ] **Step 5: Commit and push**

```bash
git add docs/API_REFERENCE.md
git commit -m "docs: mark public catalogue endpoints as implemented"
git push
```

---

## Self-Review Notes

- **Spec coverage:** §3 (cursor pagination) → Task 1; §4 (response shapes, badge derivation) →
  Tasks 2, 4, 5, 6; §5 (filters) → Task 5; §6 (publish validation) → Task 3; §7 (new files) →
  Tasks 4-7; §8 (testing) → every task's verification step + Task 8's live smoke test.
- **Placeholder scan:** no TBD/TODO; every step has complete, runnable code.
- **Type consistency check:** `InvalidCursorError`, `ArtworkNotPublishableError`,
  `PUBLIC_STATUSES`, `_artwork_card_dict` are each defined exactly once (Tasks 5, 3, 5, 5
  respectively) and referenced by the same name everywhere else they're used (Task 6 imports
  nothing new — it's appended to the same file Task 5 creates, so `PUBLIC_STATUSES` and
  `_artwork_card_dict` are already in scope). `encode_cursor`/`decode_cursor` (Task 1) and
  `compute_badge` (Task 2) are consumed with matching signatures everywhere they're called.
- **Known, deliberately-not-fixed gaps** (all called out inline, not silently dropped): no cache
  layer for `/artworks/featured` (PRD §6 mentions 60s caching; no caching infra exists in this
  codebase yet); `price_asc`/`price_desc`/`ending_soon` sorts have little-to-no real data to
  demonstrate against until Studio/auctions exist; a draft artwork that fails the rule-10 publish
  check has no in-product way to get fixed yet (no Studio edit endpoint).
