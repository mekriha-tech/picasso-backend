# Admin Panel + Artist Application Lifecycle — Design

**Date:** 2026-09-05
**Status:** Approved for planning
**Depends on:** `docs/PRD.md` §2 rules 1–10, §3.3–§3.5, §4.6, §4.8; `docs/API_REFERENCE.md` Phase 2

## 1. Problem

The user asked for an admin panel to "approve artists/paintings." Nothing to approve exists yet —
`artist_applications`, `artist_profiles`, and `artworks` aren't in the database. Building a
panel that's actually useful means building the real submission pipeline underneath it, not just
a UI over fake data (decided in brainstorming: "build the real submission API too").

## 2. Scope

**In scope:**
- `artist_applications` + `application_works` + `artist_profiles` + `artworks` +
  `artwork_images` tables
- The artist-facing submission API (PRD §4.6): create/update a draft application, fill/clear
  work slots, submit
- The admin-facing JSON API (PRD §4.8, subset): list applications, claim, approve, reject; list
  + change status of artworks
- A server-rendered HTML admin panel at `/admin/*` (separate from `/api/v1/*`) covering exactly
  those admin actions, session-cookie authenticated
- Rule 5's transaction: approving an application creates `artist_profiles` + 3 draft `artworks`
  (with their `artwork_images`) in one commit

**Out of scope (explicitly deferred, not because it's forgotten):**
- Artist Studio (PRD §4.7) — artists editing/publishing their own artworks, image *upload*
  (presigned S3). The 3 artworks created on approval are `status='draft'`; nothing here
  publishes them. That's Phase 3.
- Anything about auctions, orders, wishlists, exhibitions.
- Rate limiting on `/auth/*`-style endpoints — matches the rest of the codebase today.
- A "reason" audit trail beyond the single `rejection_reason` column already in the PRD schema.

## 3. Data model

Straight from PRD §3.4/§3.3/§3.5 — reproduced here because CLAUDE.md says the schema's
constraints encode domain rules and must not be simplified away.

### `artist_applications` (new model + migration)
```
id, user_id (FK users, CASCADE), status (application_status enum: draft/submitted/
under_review/approved/rejected), full_name, location, primary_medium, years_practising,
website_url, instagram, statement, submitted_at, reviewed_at, reviewed_by (FK users),
rejection_reason, created_at, updated_at
```
Partial unique index `one_open_application_per_user` on `(user_id) WHERE status IN
('draft','submitted','under_review')` — enforces rule 3 at the DB level; the service layer
also pre-checks so the error message is friendly, but the index is the real guarantee.

### `application_works` (new model + migration)
```
id, application_id (FK, CASCADE), slot_index (SMALLINT, CHECK 0-2), title, year, medium,
dimensions, image_url, UNIQUE(application_id, slot_index)
```

### `artist_profiles` (new model + migration)
```
id, user_id (FK users, UNIQUE, CASCADE), display_name, slug (UNIQUE), primary_medium,
years_practising, statement, website_url, instagram, cover_image_url, is_featured,
approved_at, created_at, updated_at
```
Created only by `approve_application()`. `display_name` ← `application.full_name`,
`primary_medium`/`years_practising`/`website_url`/`instagram`/`statement` copied across.
`slug` = slugify(display_name), with `-2`, `-3`, ... appended on collision.

### `artworks` (new model + migration)
```
id, artist_id (FK artist_profiles, RESTRICT), title, slug (UNIQUE), description, year,
medium, dimensions, width_cm, height_cm, category, listing_type (default 'display'),
status (default 'draft'), price, sold (default false), sold_price, sold_at,
primary_image_url, view_count (default 0), published_at, created_at, updated_at, deleted_at,
CHECK sale_needs_price, CHECK display_has_no_price
```
The 3 created on approval: `listing_type='display'`, `status='draft'`, one per
`application_works` row (`title`/`year`/`medium`/`dimensions` copied, `primary_image_url` =
the work's `image_url`). `slug` = slugify(title), same collision handling as above.

### `artwork_images` (new model + migration)
```
id, artwork_id (FK artworks, CASCADE), url, alt_text, sort_order (default 0),
is_primary (default false), width_px, height_px
UNIQUE INDEX one_primary_image_per_artwork ON (artwork_id) WHERE is_primary
```
One row per auto-created artwork, `is_primary=true`, `url` = the work's `image_url`. This
exists now (not deferred to Studio) so PRD rule 10's "publishing requires ≥1 image" has real
data to check once Studio lands — avoids a backfill later.

## 4. Service layer

New module `app/services/artist_applications.py` (functions take an `AsyncSession` and plain
arguments, no FastAPI types — keeps them callable from both the JSON API and the HTML panel):

- `get_or_create_draft_application(db, user) -> ArtistApplication` — the GET/POST
  `/me/artist-application` behavior: returns the existing draft/submitted/under_review
  application, or creates a new `draft` row if none exists. Rule 3's uniqueness means "the
  user's one open application," so POST is idempotent-ish (upserts the draft fields).
- `set_application_work(db, application, slot_index, data) -> ApplicationWork`
- `clear_application_work(db, application, slot_index) -> None`
- `submit_application(db, application) -> ArtistApplication` — validates rules 1–2 (exactly 3
  works attached, `primary_medium`/`full_name`/`location` present), raises the exact PRD §4.6
  error shape on failure (`422 {"works": ["Submit three works…"]}` /
  `{"primary_medium": ["Tell us your primary medium."]}`), sets `status='submitted'`,
  `submitted_at=now()`, `users.artist_status='pending'` (rule 4). Also enforces rule 6's 30-day
  reapply cooldown here: the partial unique index only blocks a *second open* application, so
  nothing at the DB level stops someone from drafting and submitting again the same day they're
  rejected. If the user's most recent application is `rejected` and `reviewed_at` is less than
  30 days ago, submit raises `422 {"detail": "You can reapply 30 days after a rejection."}`.
- `list_applications(db, status=None) -> list[ArtistApplication]`
- `claim_application(db, application, admin_user) -> ArtistApplication` — `submitted →
  under_review`
- `approve_application(db, application, admin_user) -> ArtistProfile` — rule 5, one
  transaction: `artist_profiles` row, 3 `artworks` + `artwork_images` rows,
  `users.artist_status='approved'`, `application.status='approved'`,
  `reviewed_at`/`reviewed_by` set.
- `reject_application(db, application, admin_user, reason) -> ArtistApplication` — rule 6:
  `status='rejected'`, `rejection_reason=reason`, `users.artist_status='rejected'`.

New module `app/services/artworks.py`:
- `list_artworks(db, status=None) -> list[Artwork]`
- `set_artwork_status(db, artwork, new_status) -> Artwork` — validates the target is a real
  `artwork_status` enum value; no further transition-graph restriction (admin has override
  power per the PRD role table), but it can't touch listing-type/price rules (8–9) since those
  aren't in scope here.

## 5. JSON API

All under `/api/v1`, all Bearer-authed via the existing `get_current_user` /
`get_current_admin_user` dependencies.

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/me/artist-application` | user | current app + 3 slots; auto-creates an empty draft if the user has none yet, so this never 404s (PRD: "the apply screen reads this") |
| POST | `/me/artist-application` | user | create/update draft fields |
| PUT | `/me/artist-application/works/{slot}` | user | `slot` is 0/1/2 |
| DELETE | `/me/artist-application/works/{slot}` | user | |
| POST | `/me/artist-application/submit` | user | 200 + application on success, 422 with PRD §4.6 copy on failure |
| GET | `/admin/applications?status=` | admin | |
| POST | `/admin/applications/{id}/claim` | admin | |
| POST | `/admin/applications/{id}/approve` | admin | |
| POST | `/admin/applications/{id}/reject` | admin | `{"reason": str}` |
| GET | `/admin/artworks?status=` | admin | |
| PATCH | `/admin/artworks/{id}` | admin | `{"status": str}` |

Schemas go in `app/schemas/artist_application.py` and `app/schemas/artwork.py`.

## 6. Admin panel (HTML, `/admin/*`, separate from `/api/v1`)

- **Auth**: Starlette `SessionMiddleware` (signed cookie, reuses `settings.SECRET_KEY`),
  entirely separate from the customer JWT/refresh-cookie system — no shared code path with the
  auth work already shipped and reviewed. `GET /admin/login` (form) / `POST /admin/login`
  (checks email + password via the same `verify_password` used by `/auth/login`, checks
  `is_admin`, sets `session["admin_user_id"]`, redirects to `/admin/applications`). `POST
  /admin/logout` clears the session. A `require_admin_session` dependency re-reads the user row
  from the DB on every request (same principle as rule 26 / `get_current_user` — a session
  merely names a user id, never a cached permission) and redirects to `/admin/login` if missing
  or no longer `is_admin`.
- **Pages** (Jinja2 templates under `app/admin_panel/templates/`, bare HTML/CSS, no JS
  framework):
  - `/admin/applications` — table: applicant, medium, status, submitted date; filter by status;
    links to detail
  - `/admin/applications/{id}` — full application + the 3 work slots (image, title, medium,
    dimensions) + Approve / Reject (reason textarea) forms, which POST to the same routes as
    the JSON API's `approve`/`reject` (the HTML forms and the JSON endpoints both call the
    identical service functions — no duplicated business logic)
  - `/admin/artworks` — table: title, artist, listing type, status; filter by status; inline
    per-row status-change form
- New router module `app/admin_panel/routes.py`, mounted in `app/main.py` at prefix `/admin`
  (distinct from `settings.API_V1_PREFIX`).

## 7. Error handling

Reuses the codebase's current convention (`HTTPException(status_code=..., detail=...)`) for the
JSON API — no attempt to fix the RFC 7807 gap flagged earlier; out of scope here. The one
place PRD wording is load-bearing is the §4.6 submit-validation copy, which must be reproduced
verbatim (CLAUDE.md rule) rather than paraphrased.

## 8. Testing

Same constraint as the auth work: no local Postgres reachable from this machine without opening
a public proxy on the database, which isn't being done routinely. Plan:
1. `ast.parse` every new/changed file.
2. Fresh-venv `pip install -r requirements.txt` after any dependency addition
   (`itsdangerous` for `SessionMiddleware`, `python-multipart` for HTML form posts — neither is
   in `requirements.txt` today).
3. After merge: live smoke test against the Railway deployment — register a test user, submit
   an application through the real API (3 works + submit), log into the admin panel, approve
   it, verify `artist_profiles`/`artworks`/`artwork_images` rows exist and `/auth/me` on that
   user now shows `artist_status: "approved"`, then exercise reject on a second test
   application, and exercise an artwork status change.

## 9. Migration order

1. `artist_applications` + `application_works`
2. `artist_profiles`
3. `artworks` + `artwork_images`

(Each is its own Alembic revision, matching the existing one-concept-per-migration pattern in
`migrations/versions/`.)
