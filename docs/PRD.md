# Picasso — Product Requirements & Backend Spec
**Stack:** FastAPI (Python 3.12) · PostgreSQL 16 · SQLAlchemy 2.0 + Alembic · Pydantic v2
**Front end:** Next.js (App Router), recreating `Picasso v2.dc.html`
**Currency:** INR only (v1). Store money as `NUMERIC(12,2)`, never float.

---

## 1. Product summary

Picasso is a curated art marketplace with a VR gallery layer. Two things make it different from a generic storefront:

1. **One account, optional artist upgrade.** There is no separate artist signup. Every user registers once as a buyer; any user can apply to become an artist by submitting three works, which a curator approves or rejects. Approval unlocks the Artist Studio.
2. **Three listing modes per artwork.** An artwork is either *for sale* at a fixed price, *up for bidding* in a timed auction against a reserve, or *on display* (portfolio or already sold, not purchasable). Listing type drives the entire UI and most business rules.

### Goals (v1)
- Collectors can browse, wishlist, buy at fixed price and bid in live auctions.
- Users can apply to be artists; curators can approve/reject.
- Approved artists can upload work, choose a listing type, run auctions and see orders.
- Artwork can be assigned to VR exhibitions.

### Explicit non-goals (v1)
Multi-currency, shipping/logistics integration, artist payouts and commission accounting, messaging between buyer and artist, mobile apps, first-party VR rendering, proxy/automatic bidding.

### Roles
| Role | How it's stored | Capabilities |
| --- | --- | --- |
| Visitor | no session | Browse, view detail, view auctions |
| User | `users` row | Everything above + wishlist, bid, buy, apply to be artist |
| Artist | `users.artist_status = 'approved'` | Everything above + Artist Studio, upload, run auctions |
| Admin | `users.is_admin = true` | Review applications, moderate artworks, cancel bids/auctions |

---

## 2. Domain rules (implement these server-side; the UI only mirrors them)

**Artist application**
1. Exactly three works must be attached before an application can be submitted.
2. `primary_medium` is required. Name and location are required.
3. A user may have at most one application in `submitted` or `under_review` at a time.
4. On submit → `users.artist_status = 'pending'`.
5. On admin approve → `artist_status = 'approved'`, `artist_profiles` row created, and the three submitted works are created as `artworks` with `listing_type = 'display'` and `status = 'draft'` so the artist can list them.
6. On reject → `artist_status = 'rejected'` with a reason; the user may reapply after 30 days.

**Sessions**
24. Refresh tokens rotate on every use, single-use. Presenting an already-rotated token means replay — revoke the whole token family, not just that token.
25. Logout, password reset, email change and admin suspension all revoke server-side; a client-side cookie clear is not sufficient.
26. Authorisation for any write is re-read from `users`, never taken from the access token's claims alone.

**Artworks**
7. Only the owning artist (or an admin) may mutate an artwork.
8. `listing_type = 'sale'` requires `price > 0`. `listing_type = 'auction'` requires exactly one auction row in a non-terminal state. `listing_type = 'display'` must have no price and no open auction.
9. An artwork with an auction that has ≥1 bid cannot change `listing_type` or be deleted — it can only be cancelled by an admin.
10. Publishing (`status: draft → published`) requires a title, at least one image, a year and a medium.

**Auctions**
11. `starts_at < ends_at`. `reserve_price > 0`.
12. Minimum next bid = `max(current_highest, starting_price) + bid_increment`. Default increment ₹500, per-auction override.
13. Bids are rejected if `now < starts_at`, `now >= ends_at`, the auction is not `live`, or the bidder is the artwork's owner.
14. Bids are **immutable**. A retraction is an admin action that writes a new row with `is_retracted = true` on the original; the highest bid is always recomputed, never edited.
15. Placing a bid must be transactional: `SELECT ... FOR UPDATE` on the auction row, re-check the minimum, insert, then update the denormalised `current_bid`/`bid_count`. Two simultaneous bids at the same amount must not both win.
16. At `ends_at`: if the highest bid ≥ `reserve_price` → auction `closed_won`, create an `orders` row for the winner with `source = 'auction'`. Otherwise → `closed_reserve_not_met`, no order. Run this from a scheduled worker (APScheduler or a Celery beat task), not from a request.
17. Anti-sniping is out of scope for v1 (no auto-extension) — note it as a follow-up.

**Orders**
18. An order is created either by a fixed-price purchase (`source = 'direct'`) or by a won auction (`source = 'auction'`).
19. A fixed-price purchase locks the artwork: `artworks.status = 'reserved'` until payment succeeds (→ `sold`) or the payment window expires (→ back to `published`).
20. When an artwork is `sold`, it flips to `listing_type = 'display'` with `sold = true` and `sold_price` set, which is exactly how the "Sold" card in the design renders.

---

## 3. PostgreSQL schema

Conventions: `id UUID PRIMARY KEY DEFAULT gen_random_uuid()` (pgcrypto), `created_at`/`updated_at TIMESTAMPTZ NOT NULL DEFAULT now()`, soft delete via `deleted_at` only where noted, all money `NUMERIC(12,2)`, all enums as native Postgres `ENUM` types.

### 3.1 Enums

```sql
CREATE TYPE artist_status   AS ENUM ('none','pending','approved','rejected');
CREATE TYPE application_status AS ENUM ('draft','submitted','under_review','approved','rejected');
CREATE TYPE listing_type    AS ENUM ('sale','auction','display');
CREATE TYPE artwork_status  AS ENUM ('draft','published','reserved','sold','unlisted','removed');
CREATE TYPE auction_status  AS ENUM ('scheduled','live','closed_won','closed_reserve_not_met','cancelled');
CREATE TYPE order_status    AS ENUM ('pending_payment','paid','cancelled','refunded','fulfilled');
CREATE TYPE order_source    AS ENUM ('direct','auction');
CREATE TYPE payment_status  AS ENUM ('created','authorized','captured','failed','refunded');
```

### 3.2 users

```sql
CREATE TABLE users (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email           CITEXT NOT NULL UNIQUE,
  password_hash   TEXT,                          -- NULL for OAuth-only accounts
  full_name       TEXT NOT NULL,
  avatar_url      TEXT,
  location        TEXT,
  phone           TEXT,
  is_admin        BOOLEAN NOT NULL DEFAULT false,
  artist_status   artist_status NOT NULL DEFAULT 'none',
  email_verified_at TIMESTAMPTZ,
  last_login_at   TIMESTAMPTZ,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX users_artist_status_idx ON users(artist_status);
```

### 3.3 artist_profiles — created on approval, 1:1 with users

```sql
CREATE TABLE artist_profiles (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id         UUID NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
  display_name    TEXT NOT NULL,
  slug            TEXT NOT NULL UNIQUE,
  primary_medium  TEXT NOT NULL,
  years_practising SMALLINT,
  statement       TEXT,
  website_url     TEXT,
  instagram       TEXT,
  cover_image_url TEXT,
  is_featured     BOOLEAN NOT NULL DEFAULT false,
  approved_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 3.4 artist_applications + application_works

```sql
CREATE TABLE artist_applications (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  status          application_status NOT NULL DEFAULT 'draft',
  full_name       TEXT NOT NULL,
  location        TEXT NOT NULL,
  primary_medium  TEXT NOT NULL,
  years_practising SMALLINT,
  website_url     TEXT,
  instagram       TEXT,
  statement       TEXT,
  submitted_at    TIMESTAMPTZ,
  reviewed_at     TIMESTAMPTZ,
  reviewed_by     UUID REFERENCES users(id),
  rejection_reason TEXT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- rule 3: only one open application per user
CREATE UNIQUE INDEX one_open_application_per_user
  ON artist_applications(user_id)
  WHERE status IN ('draft','submitted','under_review');

CREATE TABLE application_works (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  application_id  UUID NOT NULL REFERENCES artist_applications(id) ON DELETE CASCADE,
  slot_index      SMALLINT NOT NULL CHECK (slot_index BETWEEN 0 AND 2),
  title           TEXT NOT NULL,
  year            SMALLINT,
  medium          TEXT,
  dimensions      TEXT,
  image_url       TEXT NOT NULL,
  UNIQUE (application_id, slot_index)
);
```

### 3.5 artworks + artwork_images

```sql
CREATE TABLE artworks (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  artist_id       UUID NOT NULL REFERENCES artist_profiles(id) ON DELETE RESTRICT,
  title           TEXT NOT NULL,
  slug            TEXT NOT NULL UNIQUE,
  description     TEXT,                    -- the "Story" tab
  year            SMALLINT,
  medium          TEXT,                    -- "Acrylic on canvas"
  dimensions      TEXT,                    -- "36 x 34 inches" (free text; see note)
  width_cm        NUMERIC(8,2),            -- optional structured form, for filtering
  height_cm       NUMERIC(8,2),
  category        TEXT,                    -- painting / drawing / mixed media / photography
  listing_type    listing_type NOT NULL DEFAULT 'display',
  status          artwork_status NOT NULL DEFAULT 'draft',
  price           NUMERIC(12,2),           -- required when listing_type='sale'
  sold            BOOLEAN NOT NULL DEFAULT false,
  sold_price      NUMERIC(12,2),
  sold_at         TIMESTAMPTZ,
  primary_image_url TEXT,                  -- denormalised for card grids
  view_count      INTEGER NOT NULL DEFAULT 0,
  published_at    TIMESTAMPTZ,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  deleted_at      TIMESTAMPTZ,
  CONSTRAINT sale_needs_price CHECK (listing_type <> 'sale' OR price IS NOT NULL),
  CONSTRAINT display_has_no_price CHECK (listing_type <> 'display' OR price IS NULL)
);
CREATE INDEX artworks_browse_idx ON artworks(status, listing_type, published_at DESC)
  WHERE deleted_at IS NULL;
CREATE INDEX artworks_artist_idx ON artworks(artist_id, status);

CREATE TABLE artwork_images (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  artwork_id      UUID NOT NULL REFERENCES artworks(id) ON DELETE CASCADE,
  url             TEXT NOT NULL,
  alt_text        TEXT,
  sort_order      SMALLINT NOT NULL DEFAULT 0,
  is_primary      BOOLEAN NOT NULL DEFAULT false,
  width_px        INTEGER,
  height_px       INTEGER
);
CREATE UNIQUE INDEX one_primary_image_per_artwork
  ON artwork_images(artwork_id) WHERE is_primary;
```

### 3.6 auctions + bids

```sql
CREATE TABLE auctions (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  artwork_id      UUID NOT NULL REFERENCES artworks(id) ON DELETE CASCADE,
  status          auction_status NOT NULL DEFAULT 'scheduled',
  starting_price  NUMERIC(12,2) NOT NULL,
  reserve_price   NUMERIC(12,2) NOT NULL,
  bid_increment   NUMERIC(12,2) NOT NULL DEFAULT 500,
  starts_at       TIMESTAMPTZ NOT NULL,
  ends_at         TIMESTAMPTZ NOT NULL,
  current_bid     NUMERIC(12,2),           -- denormalised highest bid
  bid_count       INTEGER NOT NULL DEFAULT 0,
  winning_bid_id  UUID,                    -- FK added after bids table
  closed_at       TIMESTAMPTZ,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT auction_window CHECK (starts_at < ends_at)
);
-- rule 8: at most one non-terminal auction per artwork
CREATE UNIQUE INDEX one_open_auction_per_artwork
  ON auctions(artwork_id) WHERE status IN ('scheduled','live');
CREATE INDEX auctions_closing_idx ON auctions(status, ends_at);

CREATE TABLE bids (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  auction_id      UUID NOT NULL REFERENCES auctions(id) ON DELETE CASCADE,
  bidder_id       UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
  amount          NUMERIC(12,2) NOT NULL CHECK (amount > 0),
  is_retracted    BOOLEAN NOT NULL DEFAULT false,
  retracted_by    UUID REFERENCES users(id),
  retracted_at    TIMESTAMPTZ,
  placed_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX bids_auction_idx ON bids(auction_id, amount DESC, placed_at ASC);
CREATE UNIQUE INDEX bids_no_duplicate_amount
  ON bids(auction_id, amount) WHERE NOT is_retracted;

ALTER TABLE auctions
  ADD CONSTRAINT auctions_winning_bid_fk
  FOREIGN KEY (winning_bid_id) REFERENCES bids(id);
```

### 3.7 orders + payments

```sql
CREATE TABLE orders (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  order_number    TEXT NOT NULL UNIQUE,    -- e.g. PIC-2026-000418
  buyer_id        UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
  artwork_id      UUID NOT NULL REFERENCES artworks(id) ON DELETE RESTRICT,
  artist_id       UUID NOT NULL REFERENCES artist_profiles(id) ON DELETE RESTRICT,
  source          order_source NOT NULL,
  auction_id      UUID REFERENCES auctions(id),
  status          order_status NOT NULL DEFAULT 'pending_payment',
  amount          NUMERIC(12,2) NOT NULL,
  platform_fee    NUMERIC(12,2) NOT NULL DEFAULT 0,
  artist_payout   NUMERIC(12,2) NOT NULL DEFAULT 0,
  shipping_name   TEXT,
  shipping_address JSONB,
  payment_due_at  TIMESTAMPTZ,             -- rule 19 expiry window
  paid_at         TIMESTAMPTZ,
  fulfilled_at    TIMESTAMPTZ,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT auction_order_has_auction
    CHECK (source <> 'auction' OR auction_id IS NOT NULL)
);
CREATE UNIQUE INDEX one_live_order_per_artwork
  ON orders(artwork_id) WHERE status IN ('pending_payment','paid','fulfilled');
CREATE INDEX orders_buyer_idx ON orders(buyer_id, created_at DESC);
CREATE INDEX orders_artist_idx ON orders(artist_id, created_at DESC);

CREATE TABLE payments (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  order_id        UUID NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
  provider        TEXT NOT NULL,           -- 'razorpay' | 'stripe' (TBD)
  provider_ref    TEXT,                    -- payment intent / order id
  status          payment_status NOT NULL DEFAULT 'created',
  amount          NUMERIC(12,2) NOT NULL,
  raw_payload     JSONB,                   -- webhook body, for reconciliation
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX payments_provider_ref_idx ON payments(provider, provider_ref);
```

### 3.8 wishlists, exhibitions, testimonials, notifications

```sql
CREATE TABLE wishlist_items (
  user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  artwork_id      UUID NOT NULL REFERENCES artworks(id) ON DELETE CASCADE,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, artwork_id)
);

CREATE TABLE exhibitions (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  title           TEXT NOT NULL,
  slug            TEXT NOT NULL UNIQUE,
  description     TEXT,
  cover_image_url TEXT,
  vr_tour_url     TEXT,                    -- Artsteps URL used by the VR block
  curator_id      UUID REFERENCES users(id),
  starts_on       DATE,
  ends_on         DATE,
  is_published    BOOLEAN NOT NULL DEFAULT false,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE exhibition_artworks (
  exhibition_id   UUID NOT NULL REFERENCES exhibitions(id) ON DELETE CASCADE,
  artwork_id      UUID NOT NULL REFERENCES artworks(id) ON DELETE CASCADE,
  wall_position   SMALLINT,
  PRIMARY KEY (exhibition_id, artwork_id)
);

CREATE TABLE testimonials (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  author_name     TEXT NOT NULL,
  author_role     TEXT,
  avatar_url      TEXT,
  quote           TEXT NOT NULL,
  sort_order      SMALLINT NOT NULL DEFAULT 0,
  is_published    BOOLEAN NOT NULL DEFAULT true
);

CREATE TABLE notifications (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind            TEXT NOT NULL,           -- outbid | auction_won | application_approved | order_paid …
  title           TEXT NOT NULL,
  body            TEXT,
  link_url        TEXT,
  read_at         TIMESTAMPTZ,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX notifications_unread_idx ON notifications(user_id, created_at DESC)
  WHERE read_at IS NULL;

CREATE TABLE audit_log (
  id              BIGSERIAL PRIMARY KEY,
  actor_id        UUID REFERENCES users(id),
  action          TEXT NOT NULL,           -- 'auction.cancel', 'application.approve', …
  entity_type     TEXT NOT NULL,
  entity_id       UUID,
  metadata        JSONB,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

**Note on `dimensions`:** the design shows free text ("36 x 34 inches"). Keep the display string, but also store `width_cm`/`height_cm` so size filtering is possible later without a migration and backfill.

### 3.9 refresh_tokens — server-side session state

Access tokens stay stateless JWTs (15 min, no DB hit). Refresh tokens are **opaque random strings with a database record**, because §4.1 requires rotation and revocation and a stateless JWT can do neither: minting a replacement does not stop the old one validating until its own `exp`, and there is no record to flag on logout. For a platform where a session can place binding bids and start payments, a logged-out or stolen token must stop working immediately.

```sql
CREATE TABLE refresh_tokens (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  token_hash      TEXT NOT NULL UNIQUE,    -- SHA-256 of the opaque token; never store the token
  family_id       UUID NOT NULL,           -- constant across a rotation chain (one login = one family)
  parent_id       UUID REFERENCES refresh_tokens(id) ON DELETE SET NULL,
  expires_at      TIMESTAMPTZ NOT NULL,
  revoked_at      TIMESTAMPTZ,
  revoked_reason  TEXT,                    -- rotated | logout | reuse_detected | password_reset | admin
  user_agent      TEXT,
  ip_address      INET,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_used_at    TIMESTAMPTZ
);
CREATE INDEX refresh_tokens_user_idx ON refresh_tokens(user_id)
  WHERE revoked_at IS NULL;
CREATE INDEX refresh_tokens_family_idx ON refresh_tokens(family_id);
CREATE INDEX refresh_tokens_expiry_idx ON refresh_tokens(expires_at)
  WHERE revoked_at IS NULL;
```

**Token format.** 32 bytes from `secrets.token_urlsafe`, not a JWT. Store only `sha256(token)`; compare with a constant-time digest match. Lifetime 7 days, sliding via rotation. Sent as an httpOnly, Secure, `SameSite=Lax` cookie scoped to `/api/v1/auth`.

**Rotation with reuse detection** (rule 24 below):
1. Look up `sha256(incoming)`. Not found, expired, or `revoked_at` set with reason `rotated` → the token was replayed. Revoke **the entire family** (`family_id`) and return 401 `session_revoked`; the client must log in again.
2. Otherwise mark the row `revoked_at = now(), revoked_reason = 'rotated'`, insert a child row with the same `family_id` and `parent_id` pointing at it, and return a new access token plus the new cookie.
3. Wrap steps 1–2 in one transaction with `SELECT ... FOR UPDATE` on the row — two parallel refreshes from the same client must not both succeed and mutually revoke the session.

**Revocation triggers.** Every one of these writes `revoked_at`:

| Event | Scope |
| --- | --- |
| `POST /auth/logout` | the presented token |
| `POST /auth/logout-all` | every unrevoked token for the user |
| Password reset completed | every token for the user |
| Reuse detected | the whole `family_id` |
| Admin suspends a user | every token for the user |
| Email changed | every token for the user |

**Cleanup.** A daily job deletes rows where `expires_at < now() - interval '30 days'`; keep the recent revoked ones so reuse detection and the session list still work.

**Access-token staleness.** Because access tokens carry `artist_status` and `is_admin` as claims, a privilege change (artist approval, admin grant, suspension) does not take effect until the next refresh — at most 15 minutes. Acceptable for approval; **not** acceptable for suspension, so any endpoint that mutates money or listings re-reads the user row rather than trusting the claim. Simplest correct rule: **treat claims as a display hint, re-check authorisation from the database on every write.**

---

## 4. API — FastAPI routes

Base path `/api/v1`. JSON only. Auth via a stateless JWT access token (15 min, `Authorization: Bearer`) plus an opaque single-use refresh token backed by the `refresh_tokens` table (§3.9) in an httpOnly cookie. All list endpoints are cursor-paginated: `?limit=24&cursor=<opaque>` → `{ "items": [...], "next_cursor": "..." }`. Errors are RFC 7807 problem+json: `{ "type", "title", "status", "detail", "errors": {field: [msg]} }`.

### 4.1 Auth
| Method | Path | Notes |
| --- | --- | --- |
| POST | `/auth/register` | `{email, password, full_name}` → user + tokens |
| POST | `/auth/login` | `{email, password}` |
| POST | `/auth/refresh` | Reads the httpOnly cookie. Rotates single-use per §3.9: revokes the presented token, issues a child in the same family. 401 `session_revoked` on replay (whole family revoked) or expiry |
| POST | `/auth/logout` | Revokes the presented refresh token server-side and clears the cookie |
| POST | `/auth/logout-all` | **Auth.** Revokes every unrevoked refresh token for the user |
| GET | `/auth/sessions` | **Auth.** Active sessions from `refresh_tokens` (created, last used, user agent, IP) for the account Profile tab |
| DELETE | `/auth/sessions/{id}` | **Auth.** Revoke one session |
| POST | `/auth/password/forgot` · `/auth/password/reset` | Emailed single-use token; a completed reset revokes all refresh tokens |
| GET | `/auth/me` | Current user incl. `artist_status`, `is_admin`, `artist_profile_id` — the front end gates `/studio` on this |

### 4.2 Public catalogue
| Method | Path | Notes |
| --- | --- | --- |
| GET | `/artworks` | Filters: `listing_type`, `category`, `artist_id`, `min_price`, `max_price`, `q`, `sort=newest\|price_asc\|price_desc\|ending_soon`. Only `status IN ('published','reserved','sold')` |
| GET | `/artworks/{slug}` | Full detail incl. images, artist summary, and embedded auction (current bid, bid count, `ends_at`) |
| GET | `/artworks/featured` | Powers the home page: returns `{sale: [...3], auction: [...3], display: [...3]}` in one call |
| GET | `/artists` · `/artists/{slug}` | Artist directory and profile with their published works |
| GET | `/exhibitions` · `/exhibitions/{slug}` | VR gallery listings |
| GET | `/testimonials` | Home page |

### 4.3 Auctions & bidding
| Method | Path | Notes |
| --- | --- | --- |
| GET | `/auctions?tab=live\|upcoming\|closed` | Powers the Bidding screen |
| GET | `/auctions/{id}` | Includes `min_next_bid` — the front end should display the server's number, not compute it |
| GET | `/auctions/{id}/bids` | Bid history; bidder names masked to initials (`A. Reyes`) unless it's you (`You`) |
| POST | `/auctions/{id}/bids` | **Auth.** `{amount}`. Requires `Idempotency-Key` header. 201 on success; 409 `bid_too_low` with `min_next_bid`, 409 `auction_closed`, 403 `own_artwork` |
| WS | `/ws/auctions/{id}` | Pushes `bid_placed`, `auction_closing`, `auction_closed`. Front end falls back to 5s polling |

The prototype's countdown is client-side from `ends_at`; keep that, but treat the server's `status` as authoritative.

### 4.4 Buyer account
| Method | Path | Notes |
| --- | --- | --- |
| GET | `/me/bids?tab=active\|won\|lost` | The account "Biddings" tab |
| GET | `/me/orders` · `/me/orders/{id}` | Orders tab |
| GET/POST/DELETE | `/me/wishlist` · `/me/wishlist/{artwork_id}` | Wishlist toggle |
| GET/PATCH | `/me/profile` | Profile tab |
| GET | `/me/notifications` · POST `/me/notifications/read` | Outbid alerts etc. |

### 4.5 Checkout
| Method | Path | Notes |
| --- | --- | --- |
| POST | `/artworks/{id}/purchase` | Fixed price only. Creates order (`pending_payment`), reserves the artwork, returns a payment intent. Idempotent per artwork+user |
| POST | `/orders/{id}/pay` | Confirms/attaches provider payment |
| POST | `/webhooks/payments/{provider}` | Signature-verified. Drives `payments.status` → `orders.status`. Must be idempotent on `provider_ref` |

### 4.6 Artist application
| Method | Path | Notes |
| --- | --- | --- |
| GET | `/me/artist-application` | Current application + its three slots (the `apply` screen reads this) |
| POST | `/me/artist-application` | Creates/updates the draft |
| PUT | `/me/artist-application/works/{slot}` | Sets slot 0–2 `{title, year, medium, dimensions, image_url}` |
| DELETE | `/me/artist-application/works/{slot}` | Clears a slot |
| POST | `/me/artist-application/submit` | Validates rules 1–3, sets `pending`. 422 with `{"works": ["Submit three works…"]}` / `{"primary_medium": ["Tell us your primary medium."]}` — reuse the prototype's exact copy |

### 4.7 Artist Studio (requires `artist_status = 'approved'`)
| Method | Path | Notes |
| --- | --- | --- |
| GET | `/studio/overview` | Stat cards + recent activity in one call |
| GET | `/studio/artworks?listing_type=` | My Artworks, with filter chips |
| POST | `/studio/artworks` | Create (draft) |
| PATCH | `/studio/artworks/{id}` | Edit; also the listing-type cycle button. Enforces rules 8–9 |
| POST | `/studio/artworks/{id}/publish` · `/unlist` | Status transitions |
| DELETE | `/studio/artworks/{id}` | Soft delete; blocked once bids exist |
| POST | `/studio/artworks/{id}/images` | Direct-to-S3: returns a presigned PUT, client uploads, then confirms |
| POST | `/studio/artworks/{id}/auction` | Create auction `{starting_price, reserve_price, bid_increment, starts_at, ends_at}` |
| PATCH | `/studio/auctions/{id}` | Editable only while `bid_count = 0` |
| GET | `/studio/auctions` · `/studio/orders` · `/studio/exhibitions` | The remaining studio tabs |

### 4.8 Admin
| Method | Path | Notes |
| --- | --- | --- |
| GET | `/admin/applications?status=` | Review queue |
| POST | `/admin/applications/{id}/claim` | → `under_review` |
| POST | `/admin/applications/{id}/approve` | Runs rule 5 in one transaction |
| POST | `/admin/applications/{id}/reject` | `{reason}` — emailed to the user |
| GET/PATCH | `/admin/artworks` | Moderation |
| POST | `/admin/auctions/{id}/cancel` · `/admin/bids/{id}/retract` | Rules 14, 9 |
| CRUD | `/admin/exhibitions` · `/admin/testimonials` | Content |

### 4.9 Background jobs
| Job | Schedule | Does |
| --- | --- | --- |
| `open_scheduled_auctions` | every minute | `scheduled → live` at `starts_at` |
| `close_due_auctions` | every minute | Rule 16: settle, create order, notify |
| `expire_unpaid_orders` | every 5 min | Rule 19: release the artwork |
| `send_outbid_notifications` | on bid (queue) | Notify the previous highest bidder |
| `auction_ending_soon` | every 15 min | 24h and 1h reminders to watchers |

---

## 5. Response shapes the front end needs

The design's artwork card needs exactly these fields, so return them from every list endpoint and avoid N+1 follow-ups:

```json
{
  "id": "…", "slug": "abstract-painting", "title": "Abstract Painting",
  "artist": { "slug": "elena-d-frost", "display_name": "Elena D' Frost" },
  "primary_image_url": "…", "medium": "Acrylic Painting", "dimensions": "36 x 34 inches",
  "year": 2025, "listing_type": "auction", "status": "published",
  "badge": "Live Auction",
  "price": null, "sold": false, "sold_price": null,
  "auction": { "id": "…", "current_bid": "16000.00", "bid_count": 14,
               "reserve_price": "12000.00", "min_next_bid": "16500.00",
               "ends_at": "2026-09-02T18:30:00Z", "status": "live" },
  "in_wishlist": true
}
```

`badge` is derived server-side so the label/colour mapping lives in one place: `auction → "Live Auction"` (red `#C60000`), `sale → "For Sale"` (blue `#1E377B`), `display + sold → "Sold"` (`#444`), `display → "On Display"` (`#6A6A6A`).

Formatting stays on the client: INR `en-IN` with a `₹ ` prefix; countdown `Dd HH:MM:SS`. Send raw decimals and ISO 8601 UTC timestamps.

---

## 6. Non-functional requirements

- **Security:** Argon2id password hashing, refresh-token rotation with reuse detection (§3.9), rate limits on `/auth/*` (10/min/IP) and bidding (30/min/user), signed webhook verification, presigned uploads restricted to `image/jpeg|png|webp` ≤ 15 MB, EXIF stripped. Never expose bidder emails.
- **Concurrency:** bidding and fixed-price purchase both use row locks + unique partial indexes as the safety net (`one_live_order_per_artwork`, `bids_no_duplicate_amount`).
- **Performance:** catalogue list p95 < 300 ms; bid placement p95 < 200 ms. Cache `/artworks/featured` and `/testimonials` for 60 s; never cache auction state.
- **Images:** originals in S3/R2, served through a CDN with derived sizes (card 640w, detail 1600w) in AVIF/WebP.
- **Observability:** structured JSON logs with request ids, Sentry, and an alert if `close_due_auctions` misses a cycle.
- **Testing:** pytest + factory fixtures; required cases — concurrent equal bids (one wins), bid at exactly `min_next_bid` (accepted), bid 1 rupee under (rejected), auction closing under reserve (no order), double purchase of one artwork (second gets 409), webhook replay (idempotent), approval creating three draft artworks, replaying a rotated refresh token (whole family revoked), refresh after logout (401), two parallel refreshes (one wins, session survives).

---

## 7. Suggested build order

1. Migrations for `users`, `artist_profiles`, `artworks`, `artwork_images`; auth + `/auth/me`.
2. Public catalogue endpoints; wire home, explore and artwork detail against real data.
3. Artist application + admin approval; the `artist_status` gate on `/studio`.
4. Studio artwork CRUD + image upload.
5. Auctions, bids, the closing worker, WebSocket updates; the Bidding screen.
6. Fixed-price checkout, payments webhook, orders on both sides.
7. Exhibitions, wishlist, notifications, testimonials.

---

## 8. Decisions still needed
1. Payment provider (Razorpay is the obvious fit for INR) — determines the `payments` columns and webhook shape.
2. Platform commission rate and whether payouts are in v1 scope.
3. Auction payment window after winning (48h assumed) and the consequence of non-payment.
4. Whether display-only works from non-approved users are allowed at all (currently no — `artworks.artist_id` requires an `artist_profiles` row).
5. Anti-sniping extension: adopt now or later.
6. VR: stay on Artsteps embeds, or build first-party rooms.
