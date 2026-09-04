# Picasso Backend — API Reference (for frontend integration)

**Last verified against:** `main` @ `60dca6b` (2026-09-05)
**Live environment:** https://picasso-backend-production.up.railway.app
**Base path:** `/api/v1` (health check is the one exception, at root `/health`)
**Full spec of record:** [`docs/PRD.md`](./PRD.md) — this document is a snapshot of *what's actually
deployed right now* vs. what PRD §4 still calls for. When the two disagree, PRD.md is the target;
this file tracks progress toward it.

Status legend: ✅ implemented & deployed · 🚧 not built yet · ⚠️ implemented but diverges from PRD

---

## 1. What's live right now

Only **auth** exists. There is no artwork, artist, auction, order, wishlist, exhibition, or
notification model/endpoint in the codebase yet — `users` and `refresh_tokens` are the only two
tables.

| Method | Path | Status |
| --- | --- | --- |
| POST | `/api/v1/auth/register` | ✅ |
| POST | `/api/v1/auth/refresh` | ✅ |
| POST | `/api/v1/auth/logout` | ✅ |
| POST | `/api/v1/auth/logout-all` | ✅ |
| GET | `/api/v1/auth/sessions` | ✅ |
| POST | `/api/v1/auth/check-email` | ✅ (not in PRD — added for frontend form validation) |
| GET | `/health` | ✅ |
| **POST** | **`/api/v1/auth/login`** | 🚧 **not built — see §3** |
| GET | `/api/v1/auth/me` | 🚧 not built |
| DELETE | `/api/v1/auth/sessions/{id}` | 🚧 not built |
| POST | `/api/v1/auth/password/forgot` · `/reset` | 🚧 not built |
| *everything in PRD §4.2–§4.9* | catalogue, auctions, orders, studio, admin | 🚧 not built |

**If you were told login exists — it doesn't yet.** Registration issues you a working access
token + refresh cookie immediately, so you can build/test the logged-in UI against `/register`
in the meantime, but there is no way to log an *existing* user back in yet.

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
- ⚠️ **Bearer-token verification isn't wired up anywhere yet.** There is no
  `get_current_user`-style dependency in the codebase. `GET /auth/sessions` and
  `POST /auth/logout-all` currently authenticate off the **refresh-token cookie**, not the
  access token, even though PRD §4.1 marks them **Auth.** (implying Bearer). Every future
  protected endpoint (studio, admin, bidding, checkout, wishlist, `/me/*`) needs real
  Bearer-token verification added before it can be built — this is effectively a blocking
  prerequisite, not a nice-to-have (see §4, Phase 1).

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

No body. ⚠️ Authenticates via the `refresh_token` cookie (see §2 caveat).
- `200` → `{"detail": "Logged out of all devices successfully"}`

### `GET /api/v1/auth/sessions`

⚠️ Authenticates via the `refresh_token` cookie (see §2 caveat).
- `200` →
  ```jsonc
  { "items": [
    { "id": "uuid", "created_at": "2026-...Z", "last_used_at": "2026-...Z",
      "user_agent": "...", "ip_address": "1.2.3.4", "is_current": true }
  ] }
  ```
- `401` → `{"detail": "Not authenticated"}` / `{"detail": "Session expired or invalid"}`

### `POST /api/v1/auth/check-email`

```jsonc
// Request
{ "email": "a@b.com" }
```
- `200` → `{ "exists": true }` — for inline "email already taken" hints on the signup form.
  Note this is an unauthenticated user-enumeration surface (anyone can probe whether an email is
  registered); acceptable for v1 per how it's being used, flagging so it's a conscious choice.

---

## 4. Priority build plan

Ordered so that nothing blocks on something later in the list. Roughly follows PRD §7, adjusted
for what's actually done and for the Bearer-auth gap above.

### Phase 1 — Finish auth (blocks everything else)
1. `get_current_user` dependency: verify the `Authorization: Bearer` JWT, re-read `is_admin` /
   `artist_status` from `users` on every write per PRD rule 26 (never trust stale claims).
2. `POST /auth/login`
3. `GET /auth/me`
4. Switch `/auth/sessions` and `/auth/logout-all` onto the new Bearer dependency instead of the
   refresh cookie (or confirm cookie-based is intentional — see the open question in the PR #1
   review).
5. `DELETE /auth/sessions/{id}`
6. `POST /auth/password/forgot` / `/reset`
7. Fix the error envelope to match PRD §4's RFC 7807 shape (or update PRD if the team decides
   the current shape is fine) — do this **before** the frontend builds its error-handling layer.

### Phase 2 — Artist identity + artwork core
8. `artist_profiles`, `artworks`, `artwork_images` tables (PRD §3.3, §3.5)
9. Public catalogue: `GET /artworks`, `/artworks/{slug}`, `/artworks/featured`, `/artists`,
   `/artists/{slug}` — home/explore/detail pages can go live against real data here
10. `artist_applications` + `application_works` tables (PRD §3.4); `/me/artist-application*`
    endpoints (rules 1–3)
11. Admin review queue: `/admin/applications*` (rule 5–6, including the three-draft-artwork
    creation on approval)

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

- You can integrate `/auth/register` today: store `access_token` in memory (not localStorage —
  15 min lifetime, refresh via the cookie-backed `/auth/refresh` on 401), rely on the browser to
  carry `refresh_token` automatically since it's `httpOnly`.
- **Don't build a login screen against a real endpoint yet** — it isn't there. Mock it, or wait
  for Phase 1 item 2.
- Don't build against the RFC 7807 error shape yet (§2) — use `error.detail` as a string for now
  and expect a follow-up change.
- Nothing beyond auth exists — no artwork/catalogue data to point the home page at yet. Phase 2
  is the next thing that unblocks real frontend work beyond the auth screens.
