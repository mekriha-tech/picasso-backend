# Picasso Backend — API Reference (for frontend integration)

**Last verified against:** `main` @ PR #14 (2026-09-06) — cross-checked against the live
deployment via a full smoke test (register → login → apply → submit → admin approve/reject →
artwork status change → publish → browse via the new public catalogue endpoints → paginate →
error cases), not just read off the code.
**Live environment:** https://picasso-backend-production.up.railway.app
**Base path:** `/api/v1` (health check is the one exception, at root `/health`)
**Full spec of record:** [`docs/PRD.md`](./PRD.md) — this document is a snapshot of *what's actually
deployed right now* vs. what PRD §4 still calls for. When the two disagree, PRD.md is the target;
this file tracks progress toward it.

Status legend: ✅ implemented & deployed · 🚧 not built yet · ⚠️ implemented but diverges from PRD

---

## 1. What's live right now

Auth (including login), the artist-application submission flow, the admin approval flow
(JSON + an HTML admin panel), and the public catalogue (artworks + artists browsing) all exist
and are deployed. There is still no auction, order, wishlist, exhibition, or notification
model/endpoint in the codebase.

| Method | Path | Status |
| --- | --- | --- |
| POST | `/api/v1/auth/register` | ✅ |
| POST | `/api/v1/auth/login` | ✅ |
| GET | `/api/v1/auth/me` | ✅ |
| POST | `/api/v1/auth/refresh` | ✅ |
| POST | `/api/v1/auth/logout` | ✅ |
| POST | `/api/v1/auth/logout-all` | ✅ |
| GET | `/api/v1/auth/sessions` | ✅ |
| DELETE | `/api/v1/auth/sessions/{id}` | ✅ |
| POST | `/api/v1/auth/check-email` | ✅ (not in PRD — added for frontend form validation) |
| GET | `/health` | ✅ |
| POST | `/api/v1/auth/password/forgot` · `/reset` | 🚧 not built — needs an email-provider decision first |
| GET / POST | `/api/v1/me/artist-application` | ✅ (see §3) |
| PUT / DELETE | `/api/v1/me/artist-application/works/{slot}` | ✅ (see §3) |
| POST | `/api/v1/me/artist-application/submit` | ✅ (see §3) |
| GET | `/api/v1/admin/applications` | ✅ (see §3) |
| POST | `/api/v1/admin/applications/{id}/claim` · `/approve` · `/reject` | ✅ (see §3) |
| GET | `/api/v1/admin/artworks` | ✅ (see §3) |
| PATCH | `/api/v1/admin/artworks/{id}` | ✅ (see §3, now enforces PRD rule 10 when publishing) |
| GET/POST | `/admin/*` (HTML admin panel, not part of the JSON API) | ✅ (see §3) |
| GET | `/api/v1/artworks` | ✅ (see §3 — filtered, sorted, cursor-paginated) |
| GET | `/api/v1/artworks/{slug}` | ✅ (see §3) |
| GET | `/api/v1/artworks/featured` | ✅ (see §3) |
| GET | `/api/v1/artists` | ✅ (see §3) |
| GET | `/api/v1/artists/{slug}` | ✅ (see §3) |
| *everything else in PRD §4.3–§4.9* | auctions, orders, wishlist, exhibitions, studio | 🚧 not built |

Login is real now: `POST /auth/login` with `{email, password}` returns the same shape as
`/register` (user + access token + refresh cookie). `GET /auth/me` returns the current user
including `artist_status`, `is_admin`, and `artist_profile_id` (currently always `null` — no
`artist_profiles` join wired into that response yet, even though the table exists post-approval;
worth a small follow-up).

---

## 2. Auth model (as actually implemented)

- **Access token**: JWT, `Authorization: Bearer <token>`, 15 min lifetime, returned in the JSON
  body of `/register` and `/refresh`. Claims: `sub` (user id), `exp`, `type: "access"`.
- **Refresh token**: opaque random string, **not** a JWT. Returned only via an `httpOnly`,
  `SameSite=Lax` cookie named `refresh_token`, scoped to path `/api/v1/auth` (so it's only ever
  sent back on auth routes — don't expect it on other requests, and don't try to read it from
  JS). `Secure` is on in production, off in local dev.
- **Rotation**: every `/auth/refresh` call invalidates the presented token and issues a new one
  (7-day sliding window). Reusing an old, already-rotated token revokes the whole session
  family and returns `401 {"detail": "session_revoked"}` — the client must send the user back to
  login. **Two refresh calls fired back-to-back with the same cookie is fine** — the server
  detects that specific race and just re-issues a fresh access token instead of killing the
  session.
- **Bearer-token verification is wired up** via `get_current_user` (`app/api/deps.py`) — decodes
  the JWT and re-reads the user row from the DB on every request (PRD rule 26: never trust a
  cached claim). `get_current_admin_user` builds on it, additionally requiring `is_admin = true`,
  and gates every `/admin/*` JSON route. `GET /auth/sessions` and `POST /auth/logout-all` both use
  it now (a change from an earlier draft of this doc, back when they still read the refresh-token
  cookie) — `/sessions` also reads the cookie, but only best-effort, to flag which row is *this*
  browser's own session (`is_current`).

### Error shape ⚠️

PRD §4 specifies RFC 7807 `problem+json` errors:
`{ "type", "title", "status", "detail", "errors": {field: [msg]} }`.

**What's actually returned today is plain FastAPI default:** `{"detail": "<message>"}`. There is
no `type`/`title`/`errors` field yet, and validation errors (422) come back in FastAPI's own
`{"detail": [{"loc": [...], "msg": ..., "type": ...}]}` shape, not the PRD's
`{"field": ["message"]}` shape. **Frontend should not build against the RFC 7807 envelope yet —
build against what's below, and expect this to change.** Worth raising with backend before the
frontend error-handling layer gets too invested in one shape or the other.

---

## 3. Implemented endpoints — request/response detail

### `POST /api/v1/auth/register`

```jsonc
// Request
{ "email": "a@b.com", "password": "string", "full_name": "string" }
```
- `201` →
  ```jsonc
  {
    "user": { "id": "uuid", "email": "a@b.com", "full_name": "string", "is_admin": false },
    "access_token": "eyJ...",
    "token_type": "bearer"
  }
  ```
  Also sets the `refresh_token` cookie.
- `400` → `{"detail": "Email already registered"}` (checked both pre-flight and via a DB
  constraint, so this is race-safe under concurrent identical registrations)
- ⚠️ No password strength validation, no email format validation beyond Pydantic's `str` (not
  `EmailStr`) — anything non-empty is accepted server-side right now.

### `POST /api/v1/auth/login`

```jsonc
// Request
{ "email": "a@b.com", "password": "string" }
```
- `200` → same shape as `/register`'s `201` (user + `access_token` + `token_type`), sets the
  `refresh_token` cookie.
- `401` → `{"detail": "Invalid email or password"}` — deliberately identical whether the email is
  unknown, the password is wrong, or the account has no password at all (OAuth-only). None of
  those cases are distinguishable from the response, on purpose.

### `GET /api/v1/auth/me`

Requires `Authorization: Bearer <access_token>`.
- `200` →
  ```jsonc
  { "id": "uuid", "email": "a@b.com", "full_name": "string", "avatar_url": null,
    "is_admin": false, "artist_status": "none", "artist_profile_id": null }
  ```
  `artist_status` is one of `none` / `pending` / `approved` / `rejected` (see the artist
  application flow below). `artist_profile_id` is currently **always `null`** — it isn't
  populated from the `artist_profiles` table yet even after approval, though that table exists
  and gets a row created on approval (§3, admin review queue). Small known gap, not a bug in the
  approval flow itself — just an unwired field on this response.
- `401` → `{"detail": "Not authenticated"}` (missing/invalid/expired token)

### `POST /api/v1/auth/refresh`

No body. Reads the `refresh_token` cookie.
- `200` → `{ "access_token": "eyJ..." }`, rotates the cookie
- `401` → `{"detail": "No refresh token"}` / `{"detail": "Invalid token"}` /
  `{"detail": "Token expired"}` / `{"detail": "session_revoked"}` (reuse detected — force
  re-login)

### `POST /api/v1/auth/logout`

No body. Reads the `refresh_token` cookie (no-op if absent).
- `200` → `{"detail": "Logged out successfully"}`, clears the cookie

### `POST /api/v1/auth/logout-all`

Requires `Authorization: Bearer <access_token>`. Revokes every refresh-token session for the
current user.
- `200` → `{"detail": "Logged out of all devices successfully"}`
- `401` → `{"detail": "Not authenticated"}`

### `GET /api/v1/auth/sessions`

Requires `Authorization: Bearer <access_token>`. Also reads the `refresh_token` cookie, if
present, purely to flag which row is *this* browser's own session — the cookie is not what
authenticates the request.
- `200` →
  ```jsonc
  { "items": [
    { "id": "uuid", "created_at": "2026-...Z", "last_used_at": "2026-...Z",
      "user_agent": "...", "ip_address": "1.2.3.4", "is_current": true }
  ] }
  ```
- `401` → `{"detail": "Not authenticated"}`

### `DELETE /api/v1/auth/sessions/{id}`

Requires `Authorization: Bearer <access_token>`. Revokes one of the caller's own sessions by its
`id` (from the list above).
- `200` → `{"detail": "Session revoked"}`
- `404` → `{"detail": "Session not found"}` (wrong id, already revoked, or belongs to someone
  else — all three look identical on purpose, so this can't be used to probe other users' session
  ids)

### `POST /api/v1/auth/check-email`

```jsonc
// Request
{ "email": "a@b.com" }
```
- `200` → `{ "exists": true }` — for inline "email already taken" hints on the signup form.
  Note this is an unauthenticated user-enumeration surface (anyone can probe whether an email is
  registered); acceptable for v1 per how it's being used, flagging so it's a conscious choice.

---

### Artist applications (`/me/artist-application*`)

All routes below require `Authorization: Bearer <access_token>` (`get_current_user`).

#### `GET /api/v1/me/artist-application`

Returns the caller's current *open* application (draft, submitted, or under_review) with its
works.
- `200` → `ArtistApplicationOut`:
  ```jsonc
  {
    "id": "uuid", "status": "draft",
    "full_name": "string", "location": "string", "primary_medium": "string",
    "years_practising": 5, "website_url": null, "instagram": null, "statement": null,
    "submitted_at": null, "reviewed_at": null, "rejection_reason": null,
    "works": [
      { "slot_index": 0, "title": "string", "year": 2024, "medium": "string",
        "dimensions": "string", "image_url": "https://..." }
    ]
  }
  ```
- `404` → `{"detail": "No application yet"}`

⚠️ **Deliberate PRD deviation:** the PRD reads as though every user should have an implicit draft
application from day one. This implementation does **not** auto-create an empty draft row on
first `GET` — `full_name`, `location`, and `primary_medium` are `NOT NULL` columns on
`artist_applications`, so an empty draft can't exist without dummy placeholder values. Instead,
`GET /me/artist-application` 404s until the applicant's first successful `POST`, which creates
the row with real data. Frontend should treat 404 here as "show the empty application form", not
as an error state.

#### `POST /api/v1/me/artist-application`

Upserts the caller's draft (create-or-update). Body is `ArtistApplicationIn`:
```jsonc
{ "full_name": "string", "location": "string", "primary_medium": "string",
  "years_practising": 5, "website_url": null, "instagram": null, "statement": null }
```
- `200` → `ArtistApplicationOut` (see shape above)
- `409` → `{"detail": "Cannot edit an application after it's been submitted."}` — only a `draft`
  can be upserted.

#### `PUT /api/v1/me/artist-application/works/{slot}`

`slot` must be `0`, `1`, or `2` (exactly three work slots). Body is `ApplicationWorkIn`:
```jsonc
{ "title": "string", "image_url": "https://...", "year": 2024, "medium": "string",
  "dimensions": "string" }
```
- `200` → `ApplicationWorkOut`
- `422` → `{"detail": "slot must be 0, 1, or 2"}`
- `404` → `{"detail": "No application yet"}` (no open application to attach the work to)
- `409` → `{"detail": "Cannot edit an application after it's been submitted."}`

#### `DELETE /api/v1/me/artist-application/works/{slot}`

Clears one work slot on the draft.
- `204` → no body
- `422` / `404` / `409` → same conditions as `PUT`, above.

#### `POST /api/v1/me/artist-application/submit`

Submits the open application for admin review. Requires exactly three works and
`full_name`/`primary_medium`/`location` to be filled in (they always are once a `POST` has
succeeded, but the check is defensive).
- `200` → `ArtistApplicationOut` with `status: "submitted"`
- `404` → `{"detail": "No application yet"}`
- `409` → `{"detail": "This application has already been submitted."}`
- `422` → field-keyed validation errors, e.g.
  ```jsonc
  { "detail": {
      "works": ["Submit three works…"],
      "primary_medium": ["Tell us your primary medium."]
  } }
  ```
  Two whole-application (not field-specific) errors use the `non_field_errors` key instead of a
  field name:
  - Reapply cooldown after a rejection: `{"detail": {"non_field_errors": ["You can reapply on
    2026-10-05."]}}`
  - Already an approved artist: `{"detail": {"non_field_errors": ["You're already an approved
    artist."]}}`

---

### Admin review queue (`/admin/applications*`, `/admin/artworks*` — JSON API)

All routes below require `Authorization: Bearer <access_token>` for a user with `is_admin = true`
(`get_current_admin_user`); non-admins get `403`. This is a **separate auth mechanism** from the
HTML admin panel described below — the JSON API always uses the Bearer/JWT scheme like the rest
of `/api/v1`, never the session cookie.

#### `GET /api/v1/admin/applications?status=<status>`

`status` is optional; when given it must be one of `draft`, `submitted`, `under_review`,
`approved`, `rejected` — an invalid value is rejected by FastAPI/Pydantic before it reaches the
database.
- `200` → `list[ArtistApplicationAdminOut]` (same shape as `ArtistApplicationOut` plus `user_id`
  and `applicant_email`)
- `422` → FastAPI's standard query-validation error body if `status` isn't a recognised value.

#### `POST /api/v1/admin/applications/{id}/claim`

Moves a `submitted` application to `under_review` and records the claiming admin.
- `200` → `ArtistApplicationAdminOut`
- `404` → `{"detail": "Application not found"}`
- `409` → `{"detail": "Only a submitted application can be claimed."}`

#### `POST /api/v1/admin/applications/{id}/approve`

Approves a `submitted` or `under_review` application: creates the `artist_profiles` row and three
`draft` `artworks` rows (one per submitted work), and flips the user's `artist_status` to
`approved`.
- `200` → `ArtistApplicationAdminOut` with `status: "approved"`
- `404` → `{"detail": "Application not found"}`
- `409` → one of:
  - `{"detail": "Only a submitted or under-review application can be approved."}`
  - `{"detail": "Application no longer has exactly three works."}`
  - `{"detail": "This application (or applicant) was already approved."}` — guards the race where
    two admins approve the same application (or the applicant is otherwise already an approved
    artist) concurrently; the second commit hits a DB conflict and is turned into this 409 instead
    of a raw 500.

#### `POST /api/v1/admin/applications/{id}/reject`

Body: `{"reason": "string"}`. Rejects a `submitted` or `under_review` application.
- `200` → `ArtistApplicationAdminOut` with `status: "rejected"`
- `404` → `{"detail": "Application not found"}`
- `409` → `{"detail": "Only a submitted or under-review application can be rejected."}`

#### `GET /api/v1/admin/artworks?status=<status>`

`status` is optional; when given it must be one of `draft`, `published`, `reserved`, `sold`,
`unlisted`, `removed` — same 422-on-invalid-value behaviour as the applications endpoint above.
- `200` → `list[ArtworkOut]`

#### `PATCH /api/v1/admin/artworks/{id}`

Body: `{"status": "published"}` (or any other `ArtworkStatus` value, as a plain string here — this
endpoint's payload is validated in the service layer, not via a Pydantic enum, so an invalid value
returns a clean `422` rather than a query-param-style FastAPI error).
- `200` → `ArtworkOut`
- `404` → `{"detail": "Artwork not found"}`
- `422` → `{"detail": "'<value>' is not a valid artwork status."}`
- `409` → `{"detail": "Artwork needs a title, year, and medium before it can be published."}` or
  `{"detail": "Artwork needs at least one image before it can be published."}` — **PRD rule 10**:
  enforced when the target status is `published`, `reserved`, or `sold` (any status the public
  catalogue below can show), not just `published` specifically. There is currently **no
  in-product way to fix a draft's missing fields** — no Studio edit endpoint exists yet — so a
  draft that fails this check needs a direct DB edit to become publishable. Worth flagging to
  whoever's testing the admin panel, since it's a real dead end today.

---

### HTML admin panel (`/admin/*`)

A server-rendered HTML panel for reviewing applications and managing artwork status, separate
from the JSON API above:

- **Prefix:** `/admin` (note: no `/api/v1` — this is not part of the JSON API surface, and
  `include_in_schema=False` keeps it out of the `/api/v1/docs` Swagger UI).
- **Auth:** session-cookie based (`admin_session` cookie via Starlette's `SessionMiddleware`,
  scoped to path `/admin`), entirely separate from the Bearer/JWT scheme used everywhere else.
  Log in at `GET/POST /admin/login` with an admin user's email + password; log out at
  `POST /admin/logout`. Visiting any `/admin/*` page without a valid session redirects to
  `/admin/login`.
- **Granting admin access is still a direct DB operation** — there is no self-service "become an
  admin" flow anywhere in the product. An operator sets `users.is_admin = true` by hand (e.g. via
  a one-off SQL statement) for whichever account should have panel access.
- **Pages:**
  - `GET /admin` → redirects to `/admin/applications` (session-gated, same as every other page).
  - `GET /admin/applications` (optional `?status=`) — list view; an unrecognised `status` value
    is treated as "no filter" rather than erroring, since this is a human-editable URL bar, not a
    JSON API.
  - `GET /admin/applications/{id}` — detail view with approve/reject actions.
  - `POST /admin/applications/{id}/approve` / `/reject` — same underlying service calls as the
    JSON routes; on a conflict (e.g. another admin already approved/rejected it), redirects back
    to the page with `?error=<message>` instead of silently succeeding.
  - `GET /admin/artworks` (optional `?status=`, same invalid-value-is-no-filter behaviour) — list
    view showing title, artist, listing type, and status, with an inline status-change form per
    row.

---

### Public catalogue (`/artworks*`, `/artists*`)

No auth on any of these — public, unauthenticated, matching PRD §4.2. Every query filters to
`status IN ('published', 'reserved', 'sold')`; `draft`/`unlisted`/`removed` artworks never appear
here regardless of other filters, no exceptions.

#### `GET /api/v1/artworks?listing_type=&category=&artist_id=&min_price=&max_price=&q=&sort=&limit=&cursor=`

All params optional.
- `listing_type`: `sale` / `auction` / `display` — invalid value → `422`
- `sort`: `newest` (default) / `price_asc` / `price_desc` / `ending_soon` — invalid value → `422`.
  ⚠️ `ending_soon` currently behaves identically to `newest` (no `auctions` table exists yet, so
  there's no real "time until close" to sort by). ⚠️ `price_asc`/`price_desc` exclude
  NULL-priced artworks entirely (sorting "by price" for something with no price is meaningless) —
  today that means these two sorts return **no results at all**, since every artwork so far is
  `listing_type='display'`, which per the `display_has_no_price` CHECK constraint always has
  `price = NULL`. This isn't a bug; it'll start returning real results once `sale`-listing
  artworks exist (Studio work, not yet built).
- `min_price`/`max_price`: numeric strings; non-numeric or non-finite (`NaN`/`Infinity`) → `400`
- `q`: plain `ILIKE '%...%'` on `title`, nothing fancier
- `limit`: default 24, max 100
- `cursor`: opaque, from a previous response's `next_cursor` — malformed/garbage → `400`
  (`{"detail": "Invalid cursor"}`), never a 500
- `200` →
  ```jsonc
  { "items": [ /* artwork cards, see shape below */ ], "next_cursor": "opaque-string-or-null" }
  ```

**Artwork card shape** (used here, in `/artworks/featured`, and embedded in `/artists/{slug}`'s
`works`):
```jsonc
{
  "id": "uuid", "slug": "sunset", "title": "Sunset",
  "artist": { "slug": "elena-frost", "display_name": "Elena Frost" },
  "primary_image_url": "https://...", "medium": "Oil", "dimensions": null,
  "year": 2024, "listing_type": "display", "status": "published",
  "badge": "On Display",
  "price": null, "sold": false, "sold_price": null,
  "auction": null, "in_wishlist": false
}
```
`price`/`sold_price` are **strings**, not JSON numbers, to avoid float precision loss — parse them
client-side if you need to do math. `auction` is always `null` (no auctions table yet) and
`in_wishlist` is always `false` (no wishlist yet, and these are unauthenticated endpoints anyway)
— both real PRD-documented fields kept present-but-inert for forward compatibility rather than
omitted, so the frontend can build its card component once against the final shape.

#### `GET /api/v1/artworks/{slug}`

Card shape plus `description`, `width_cm`, `height_cm` (also strings), `category`, `view_count`,
and the full `images` array (`url`, `alt_text`, `sort_order`, `is_primary`) instead of just
`primary_image_url`.
- `200` → detail shape above
- `404` → `{"detail": "Artwork not found"}` (includes non-public statuses — a `draft` artwork's
  slug 404s here even though `GET /admin/artworks` can see it)

#### `GET /api/v1/artworks/featured`

No params. Powers the homepage.
- `200` → `{ "sale": [...up to 3 cards], "auction": [...up to 3], "display": [...up to 3] }`,
  each ordered newest-first. `sale`/`auction` are empty arrays today — nothing creates those
  listing types yet (Studio/auction work, not built) — that's correct, not a bug.

#### `GET /api/v1/artists?limit=&cursor=`

Only artists with **at least one publicly-visible artwork** appear here (a deliberate default —
no reason to show an empty directory entry for someone with nothing published yet).
- `200` →
  ```jsonc
  { "items": [
    { "slug": "elena-frost", "display_name": "Elena Frost", "primary_medium": "Oil painting",
      "cover_image_url": null, "is_featured": false }
  ], "next_cursor": "opaque-string-or-null" }
  ```

#### `GET /api/v1/artists/{slug}`

Card shape plus `statement`, `years_practising`, `website_url`, `instagram`, `approved_at`, and
`works` (their published artworks, using the artwork card shape above, unpaginated — a
reasonable list size for one artist's page). **No** publicly-visible-artwork filter here (unlike
the list endpoint) — a direct link to an approved artist's profile resolves even before they have
anything published, just with an empty `works` array.
- `200` → detail shape above
- `404` → `{"detail": "Artist not found"}`

---

## 4. Priority build plan

Ordered so that nothing blocks on something later in the list. Roughly follows PRD §7, adjusted
for what's actually done and for the Bearer-auth gap above.

### Phase 1 — Finish auth ✅ done except password reset
1. ✅ `get_current_user` dependency: verifies the `Authorization: Bearer` JWT, re-reads `is_admin`
   / `artist_status` from `users` on every request per PRD rule 26 (never trust stale claims).
2. ✅ `POST /auth/login`
3. ✅ `GET /auth/me`
4. ✅ `/auth/sessions` and `/auth/logout-all` now use the Bearer dependency.
5. ✅ `DELETE /auth/sessions/{id}`
6. 🚧 `POST /auth/password/forgot` / `/reset` — still needs an email-provider decision first.
7. 🚧 Fix the error envelope to match PRD §4's RFC 7807 shape (or update PRD if the team decides
   the current shape is fine) — do this **before** the frontend builds its error-handling layer.
   Still not done; see the Error shape note in §2.

### Phase 2 — Artist identity + artwork core ✅ done
8. ✅ `artist_profiles`, `artworks`, `artwork_images` tables (PRD §3.3, §3.5)
9. ✅ Public catalogue: `GET /artworks`, `/artworks/{slug}`, `/artworks/featured`, `/artists`,
   `/artists/{slug}` — home/explore/detail pages can go live against real data now. `sale`/
   `auction` listings and price sorting won't have real data until Studio (Phase 3) lets an
   artist create something other than a `display` listing.
10. ✅ `artist_applications` + `application_works` tables (PRD §3.4); `/me/artist-application*`
    endpoints (rules 1–3)
11. ✅ Admin review queue: `/admin/applications*` (rule 5–6, including the three-draft-artwork
    creation on approval) plus a session-cookie HTML admin panel at `/admin/*` (not in the
    original PRD, added for operational convenience) covering the same actions, now with PRD
    rule 10 enforced before anything can become publicly visible.

### Phase 3 — Studio
12. `/studio/artworks` CRUD, `/publish`, `/unlist`, listing-type rules 8–9
13. `/studio/artworks/{id}/images` presigned-upload flow

### Phase 4 — Auctions & bidding
14. `auctions` + `bids` tables (PRD §3.6)
15. `/auctions*`, `/studio/artworks/{id}/auction`, `/studio/auctions/{id}`
16. Bid placement with `SELECT ... FOR UPDATE` (rule 15) — reuse the row-locking pattern already
    proven out in `/auth/refresh`
17. `open_scheduled_auctions` / `close_due_auctions` background jobs, `/ws/auctions/{id}`

### Phase 5 — Checkout
18. `orders` + `payments` tables (PRD §3.7)
19. `/artworks/{id}/purchase`, `/orders/{id}/pay`, `/webhooks/payments/{provider}`
20. `expire_unpaid_orders` job

### Phase 6 — Everything else
21. `/me/bids`, `/me/orders`, `/me/wishlist`, `/me/profile`, `/me/notifications`
22. `/exhibitions`, `/testimonials`
23. Remaining notification jobs (outbid, ending-soon reminders)

---

## 5. For the frontend dev, right now

- **Login and signup are both real** — `/auth/register` and `/auth/login` return the same shape;
  store `access_token` in memory (not localStorage — 15 min lifetime, refresh via the
  cookie-backed `/auth/refresh` on 401), rely on the browser to carry `refresh_token`
  automatically since it's `httpOnly`. Use `/auth/check-email` for inline "email already taken"
  validation on the signup form, and `GET /auth/me` to hydrate the logged-in session on page load.
- **The "apply to be an artist" flow is real too** — `/me/artist-application` (create/update),
  `/me/artist-application/works/{slot}` (the 3 work-sample slots), and `/submit`. Remember `GET`
  404s until the applicant's first successful `POST` — that's the "show the empty form" signal,
  not an error.
- Don't build against the RFC 7807 error shape yet (§2) — use `error.detail` as a string (it's
  sometimes a string, sometimes an object keyed by field name — see the artist-application
  `422`s above) and expect a follow-up change.
- **The public catalogue is real now** — `GET /artworks` (filters, sort, cursor pagination),
  `/artworks/{slug}`, `/artworks/featured` (homepage), `/artists`, `/artists/{slug}`. No auth
  needed on any of these. Right now there's a handful of real published artworks under one
  artist to build against on the live deployment; `sale`/`auction` listings and price-sorted
  results will stay empty until Studio work lands, so don't build the "for sale" / "live auction"
  sections expecting real data yet — `display`-listing browsing is what's real today.
  `/testimonials` still doesn't exist (Phase 6).
- Social login buttons (Apple/Google/Facebook), if they're in your designs, aren't backed by
  anything — there's no OAuth flow in the PRD or this codebase. Worth a separate conversation
  before wiring those up to anything real.
