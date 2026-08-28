# Review: Initial Auth/DB Setup

**Commit reviewed:** `1c48329` — "feat: initial setup with postgres, alembic, and auth register api"
**Author:** Mayank Sharma
**Reviewed against:** `docs/PRD.md` §2 rules 24–26, §3.2 (users schema), §3.9 (refresh_tokens, new), §4.1 (auth routes), §6 (non-functional requirements)
**Reviewed by:** Bhaskar (via Claude), 2026-08-29 — updated same day after PRD revision

> **Update note:** the PRD was revised after the first pass of this review to add §3.9 (`refresh_tokens`) and rules 24–26. That revision formalizes exactly the gap flagged as "point 5" below — it's no longer an open design question, it's now a specified table and flow the code needs to implement. Findings 1–4 are unchanged from the first pass. Finding 5 is rewritten against the new spec.

## Scope of the commit

First implementation slice toward PRD §7 build-order item 1: the `users` table migration, the `User` SQLAlchemy model, `POST /auth/register`, JWT helpers, and app config/bootstrap.

## Summary

The shape of the work is correct and matches the spec closely — the table columns, the `artist_status` enum, the register endpoint's path and payload, and Argon2id hashing all line up with PRD §3.2 / §4.1. There are five gaps against the spec, four of which are small and mechanical, and one (refresh token design) that's a decision to make before the next two auth endpoints are built.

## Findings

### 1. `email` column is `String`, not `CITEXT` — correctness
PRD §3.2: `email CITEXT NOT NULL UNIQUE`. As implemented it's a plain `String`/`VARCHAR` with a case-sensitive unique index. `Foo@x.com` and `foo@x.com` would register as two separate accounts, which breaks the "one account per person" model the PRD's product summary (§1) relies on.

**Fix:** use `sqlalchemy.dialects.postgresql.CITEXT` for the column, `CREATE EXTENSION IF NOT EXISTS citext` in a migration.

### 2. Missing `users_artist_status_idx` — spec gap
PRD §3.2 defines `CREATE INDEX users_artist_status_idx ON users(artist_status);` for admin review-queue and studio-gating queries. The migration only creates the email unique index.

**Fix:** add the index in a follow-up migration.

### 3. `id` default is Python-side, not DB-side — convention gap
PRD's stated convention (§3, "Conventions") applies `id UUID PRIMARY KEY DEFAULT gen_random_uuid()` to every table, via the `pgcrypto` extension. This model uses `default=uuid.uuid4` (Python-side), so rows inserted outside the ORM won't get an id.

**Fix:** add `server_default=text("gen_random_uuid()")` on the `id` column (Python-side default can stay as a belt-and-suspenders fallback).

### 4. Race condition on duplicate-email check — concurrency
`register_user` does `SELECT` for an existing email, then `INSERT`s. Two concurrent registrations with the same email can both pass the check; the losing insert hits the unique constraint and raises an unhandled `IntegrityError`, surfacing as a 500 instead of a clean 400 "email already registered." CLAUDE.md calls out concurrency-critical paths explicitly (bidding/purchase) — this is the same bug class on a simpler path, and cheap to close now.

**Fix:** catch `IntegrityError` around the insert (or wrap in `try/except` and re-check) and return 400 on conflict.

### 5. Refresh token doesn't match the (now-formalized) spec — needs implementation
**This is no longer an open question — the PRD update adds §3.9 and rules 24–26 that spec this out exactly.** The code as it stands diverges from it in every particular:

| §3.9 requires | Current code (`app/core/security.py`, `app/api/v1/auth.py`) |
| --- | --- |
| Opaque random token (`secrets.token_urlsafe(32)`), not a JWT | `create_refresh_token()` mints a signed JWT with `type: "refresh"` |
| A `refresh_tokens` row per token: `token_hash` (SHA-256), `family_id`, `parent_id`, `expires_at`, `revoked_at`, `revoked_reason`, `user_agent`, `ip_address` | No table exists, no migration for it; `register_user` never writes a session record |
| Rotation on every use: revoke presented token, insert child in same `family_id`, under `SELECT ... FOR UPDATE` | No `/auth/refresh` endpoint yet — nothing to rotate |
| Reuse detection: presenting an already-rotated token revokes the whole family and returns 401 `session_revoked` (rule 24) | Not implemented — can't be, without the table |
| `/auth/logout`, `/auth/logout-all`, `/auth/sessions`, `DELETE /auth/sessions/{id}` all read/write `refresh_tokens` | None of these endpoints exist yet |
| Cookie scoped to `/api/v1/auth`, `Secure`, `SameSite=Lax` | Cookie in `register_user` has no `path=` restriction and `secure=False` hardcoded |

**Why the original stateless-JWT approach couldn't have worked (background, now moot given the spec update):** a JWT is verified purely by signature + `exp`, with no database lookup. "Rotating" it just means minting a new one — the old one keeps validating until its own expiry regardless. "Revoking" it requires flagging a server-side record, and a bare JWT has none. §3.9's design (opaque token, hashed, row per token, family-based reuse detection) is the standard fix and is what's now specified — it just isn't built yet.

**What's needed:** treat this as its own build item — `refresh_tokens` migration + model, a `SessionService` (or similar) implementing the rotation/reuse-detection algorithm in §3.9 step 1–3, and the `/auth/refresh`, `/auth/logout`, `/auth/logout-all`, `/auth/sessions` endpoints from the updated §4.1 table. `register_user` and the (not-yet-built) `/auth/login` should both create the initial `refresh_tokens` row (`family_id` = new UUID, `parent_id` = null) instead of just signing a JWT.

Also relevant now: **rule 26** ("authorisation for any write is re-read from `users`, never taken from the access token's claims alone") doesn't affect this commit yet since there are no protected write endpoints, but it's worth flagging early since it constrains how `/auth/me`-gated routes should be built later — don't trust `is_admin`/`artist_status` from the JWT payload for authorization decisions, only for display.

### Minor / non-blocking
- `UserCreate.email` is `str`, not Pydantic's `EmailStr` — no format validation at the schema layer.
- Cookie `secure=False` is hardcoded (comment notes prod should flip it) rather than derived from `settings.ENVIRONMENT`.
- Rate limiting on `/auth/*` (§6: 10/min/IP) isn't implemented yet — expected at this stage, just not yet covered.

## Suggested priority

1–4 are small, mechanical, and worth doing before more tables FK into `users` or more auth code lands on top of the register endpoint.
5 is now a concrete, spec'd piece of work (§3.9), not an open decision — it should land before `/auth/login`, `/auth/refresh`, or `/auth/logout` are built, since retrofitting session storage after those endpoints exist means an extra migration and a breaking change to the token shape clients already integrated against. Recommend sequencing it as: `refresh_tokens` migration → update `register_user` to create the initial session row → build `/auth/refresh`/`/auth/logout`/`/auth/logout-all`/`/auth/sessions` per §4.1.

## Verification (once fixes land)
- `alembic upgrade head` locally; confirm `\d users` shows the `citext` type, `gen_random_uuid()` default, and the new index; confirm `refresh_tokens` exists with the columns/indexes in §3.9.
- Register the same email with different casing twice — second attempt should be rejected.
- Two concurrent registrations with the same email — one should get a clean 400, not a 500.
- The three session test cases §6 now calls out explicitly: replaying a rotated refresh token revokes the whole family (401 `session_revoked`); refreshing after logout returns 401; two parallel `/auth/refresh` calls on the same token — exactly one succeeds, the session survives (no false family-revoke).
