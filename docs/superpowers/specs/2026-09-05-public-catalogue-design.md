# Public Catalogue Endpoints — Design

**Date:** 2026-09-05
**Status:** Approved for planning
**Depends on:** `docs/PRD.md` §2 rule 10, §3.5 (artworks/artwork_images), §3.3 (artist_profiles), §4.2,
§5; `docs/API_REFERENCE.md` Phase 2 item 9 (flagged as the biggest gap blocking real frontend work)

## 1. Problem

Nothing in the codebase lets a visitor browse artworks or artists. The homepage/explore/artist
pages the frontend needs to build have no real data source — everything today is mocked. Every
artwork currently in the DB is also `status='draft'` (auto-created on artist approval, never
published), so this work needs a minimal way to publish one, or the new endpoints would have
nothing to return even once built.

## 2. Scope

**In scope:**
- `GET /api/v1/artworks` — filtered, sorted, cursor-paginated public list
- `GET /api/v1/artworks/{slug}` — public detail
- `GET /api/v1/artworks/featured` — `{sale, auction, display}` grouped, for the homepage
- `GET /api/v1/artists` — public list (only artists with ≥1 published artwork)
- `GET /api/v1/artists/{slug}` — public detail + their published works
- Validation added to the **existing** `PATCH /api/v1/admin/artworks/{id}` (`app/services/artworks.py::set_artwork_status`): PRD rule 10 (title, ≥1 image, year, medium present) enforced only when the target status is `published`
- A reusable cursor-pagination helper (`app/services/pagination.py`) and a reusable badge-derivation helper (`app/services/badges.py`) — both pure, both unit-tested

**Explicitly out of scope:**
- Artist Studio (an artist publishing their own work) — publishing stays an admin action via the endpoint above, same as today's status changes
- Auctions, orders, wishlist — every field PRD §5 shows for those stays a `null` placeholder
- Full-text search quality — `q` is a plain `ILIKE '%...%'` on `title`, nothing fancier
- Caching (PRD §6 mentions 60s caching for `/artworks/featured`/`/testimonials`) — no cache layer exists in this codebase; noted as a follow-up, not built here

## 3. Cursor pagination

PRD's sort options (`newest`/`price_asc`/`price_desc`/`ending_soon`) rule out a plain "last id"
cursor, since the tie-breaker column changes per sort. The cursor is a base64-encoded JSON pair
`{"v": <sort-column value, ISO-formatted if a date>, "id": "<uuid>"}`, decoded into a keyset
`WHERE (sort_col, id) < (v, id)` (or `>` depending on direction) clause — this is standard keyset
pagination: no `OFFSET`, stays fast as the table grows, and correctly supports every sort order.

`app/services/pagination.py`:
```python
encode_cursor(sort_value: Any, row_id: uuid.UUID) -> str
decode_cursor(cursor: str) -> tuple[Any, uuid.UUID] | None   # None on anything malformed
```
An artwork/artist list route calls `decode_cursor`; a `None` result (bad/tampered cursor) is a
`400`, not a 500 or a silently-ignored filter.

`ending_soon` has no real distinguishing data yet (no `auctions` table) — implemented as an
accepted enum value that behaves identically to `newest` for now (every artwork today is
`listing_type='display'` anyway); documented as a known limitation, not an error.

## 4. Response shapes

### Artwork card (list + embedded-in-artist-detail)
Matches PRD §5 exactly:
```jsonc
{
  "id": "uuid", "slug": "abstract-painting", "title": "Abstract Painting",
  "artist": { "slug": "elena-d-frost", "display_name": "Elena D' Frost" },
  "primary_image_url": "...", "medium": "...", "dimensions": "...",
  "year": 2025, "listing_type": "display", "status": "published",
  "badge": "On Display",
  "price": null, "sold": false, "sold_price": null,
  "auction": null,
  "in_wishlist": false
}
```
`auction` is always `null` (no auctions table yet). `in_wishlist` is always `false` — these are
public, unauthenticated endpoints (no user context to check against), and wishlist doesn't exist
yet either; both are real PRD-documented fields kept present-but-inert for frontend
forward-compatibility, per the earlier design conversation.

### Artwork detail
Card shape plus: `description`, `width_cm`, `height_cm`, `category`, `view_count`, and the full
`artwork_images` list (`url`, `alt_text`, `sort_order`, `is_primary`) instead of just
`primary_image_url`.

### Badge derivation (`app/services/badges.py`)
Pure function `compute_badge(listing_type: str, status: str, sold: bool) -> str`, per PRD §5's
table: `auction` → `"Live Auction"`, `sale` → `"For Sale"`, `display` + `sold` → `"Sold"`,
`display` (not sold) → `"On Display"`. (Color hex codes from the PRD are a frontend styling
concern, not part of this API's response.)

### Artist card / detail
Card: `slug`, `display_name`, `primary_medium`, `cover_image_url`, `is_featured`. Detail adds:
`statement`, `years_practising`, `website_url`, `instagram`, `approved_at`, plus `works: [artwork
card, ...]` (their published artworks, using the same card shape above, no pagination — a
reasonable list size for a single artist's page).

### `GET /artworks/featured`
```jsonc
{ "sale": [ /* up to 3 cards */ ], "auction": [ /* up to 3 */ ], "display": [ /* up to 3 */ ] }
```
Three independent queries (one per `listing_type`, `status='published'`, `ORDER BY published_at
DESC LIMIT 3`) — `sale`/`auction` will be empty arrays today (nothing creates those listing
types yet), which is correct, not a bug.

## 5. Filters (`GET /artworks`)

Query params, all optional except pagination: `listing_type`, `category`, `artist_id`,
`min_price`, `max_price`, `q` (title `ILIKE`), `sort` (`newest` default /`price_asc`/
`price_desc`/`ending_soon`), `limit` (default 24, max 100), `cursor`. Always filters to `status IN
('published', 'reserved', 'sold')` per PRD §4.2 — `draft`/`unlisted`/`removed` never appear here
regardless of other filters.

## 6. The publish-validation addition

`set_artwork_status` (`app/services/artworks.py`, already exists) gains one check: when
`new_status == ArtworkStatus.published`, first verify the artwork has a non-empty `title`, a
`year`, a `medium`, and at least one `artwork_images` row — raise a new
`ArtworkNotPublishableError` (mapped to `409` by both the JSON admin route and the HTML admin
panel, same pattern as the existing `InvalidArtworkStatusError`) if any are missing. Every other
status transition is untouched.

## 7. New files

- `app/services/pagination.py` — `encode_cursor`/`decode_cursor` (pure, tested)
- `app/services/badges.py` — `compute_badge` (pure, tested)
- `app/services/catalogue.py` — `list_public_artworks`, `get_artwork_by_slug`,
  `get_featured_artworks`, `list_artists`, `get_artist_by_slug` (DB-touching, verified via live
  smoke test per the existing pattern in this repo)
- `app/schemas/catalogue.py` — `ArtworkCardOut`, `ArtworkDetailOut`, `ArtworkImageOut`,
  `ArtistCardOut`, `ArtistDetailOut`, `FeaturedArtworksOut`, `PaginatedArtworksOut`
- `app/api/v1/catalogue.py` — the 5 new routes, mounted in `app/main.py` alongside the existing
  routers
- Modify `app/services/artworks.py` — add `ArtworkNotPublishableError` + the rule-10 check inside
  `set_artwork_status`
- Modify `app/api/v1/admin.py` and `app/admin_panel/artwork_routes.py` — map the new exception to
  `409` (JSON) / the existing `?error=` redirect pattern (HTML), same shape as the existing
  `InvalidArtworkStatusError` handling

No new migrations — `artworks`, `artwork_images`, `artist_profiles` all already exist.

## 8. Testing

- `ast.parse` every new/changed file; `alembic upgrade head --sql` re-run as a no-op sanity check
  (confirms nothing in the schema was accidentally touched)
- Real pytest unit tests for `encode_cursor`/`decode_cursor` (round-trip, malformed input) and
  `compute_badge` (all 4 PRD-table cases)
- Live smoke test once merged/deployed (no local Postgres in this dev environment, same
  constraint as every prior piece of work): publish an artwork via the admin panel (exercising
  the new rule-10 validation, including a deliberate failure case — try publishing one missing a
  medium and confirm `409`), then hit all 5 new endpoints against the real deployment and confirm
  filtering, sorting, and cursor pagination (fetch page 1, follow `next_cursor` to page 2, confirm
  no overlap/gap) all behave correctly.
