# Start here — picasso-backend

Repo: https://github.com/mekriha-tech/picasso-backend

## Layout

Unzip this bundle into `docs/` at the repo root:

```
picasso-backend/
├─ CLAUDE.md            ← create this (contents below)
├─ docs/
│  ├─ PRD.md            ← product requirements, schema, API spec
│  └─ START_HERE.md     ← this file
├─ app/
├─ alembic/
└─ tests/
```

## CLAUDE.md (create at repo root)

```markdown
# Picasso — Backend

API for an art marketplace with a VR gallery layer. Frontend lives in a separate
repo (mekriha-tech/picasso-frontend) and consumes this API only.

Spec: docs/PRD.md — schema in §3, endpoints in §4, build order in §7.
The schema's CHECK constraints and partial unique indexes encode domain rules;
do not simplify them away.

Stack: FastAPI, PostgreSQL 16, SQLAlchemy 2.0 (async), Alembic, Pydantic v2.
Money is NUMERIC(12,2) — never float. Timestamps are TIMESTAMPTZ, API returns
ISO 8601 UTC. Currency is INR; formatting is the frontend's job.

Rules:
- Every endpoint in §4 must match its documented path, method and status codes —
  the frontend is built against them independently.
- Validation error copy in §4.6 is user-facing and fixed; reuse it verbatim.
- Business rules live in the service layer, not in route handlers.
- Bidding and purchase are concurrency-critical: row locks per §2 rules 15 and 19.
```

## First prompt to Claude Code

Read `docs/PRD.md` in full, then:

> I'm building the Picasso backend per docs/PRD.md. Start with step 1 of the build order in §7:
>
> 1. Scaffold the project — FastAPI app factory, pydantic-settings config, async SQLAlchemy session, Alembic, docker-compose with Postgres 16, ruff + pytest.
> 2. Write the initial migration for the enums in §3.1 and the tables `users`, `artist_profiles`, `artworks`, `artwork_images` in §3.2–3.5, exactly as specified including CHECK constraints and partial unique indexes.
> 3. Implement auth (§4.1): Argon2id hashing, JWT access token (15 min) plus rotating refresh token in an httpOnly cookie, and `GET /auth/me` returning `artist_status`, `is_admin` and `artist_profile_id`.
> 4. Add a pytest fixture that spins up a Postgres test database, and cover register/login/refresh/me.
>
> Show me the plan and the migration before implementing. Commit in reviewable steps.

Then drive the remaining steps one at a time:

> Continue with step N of the build order in docs/PRD.md §7.

## Order of work and the handoff to the frontend

The frontend developer is blocked on endpoint shapes, not on their implementation. So before step 2, produce the contract:

> Generate the full OpenAPI schema for every endpoint in docs/PRD.md §4 as Pydantic response models with realistic examples, even where the handlers are still stubs returning 501. Export it to `docs/openapi.json` and commit it.

Regenerate and commit `docs/openapi.json` on every PR that changes an endpoint. That file is what the frontend developer generates their TypeScript types from — it is the interface between the two repos.

Also worth standing up early: a seeded dev database matching the prototype's sample data (3 auctions, 3 for-sale, 3 display works), so the frontend has something real to render.

> Add an `alembic`-independent seed script (`scripts/seed_dev.py`) creating an admin, two approved artists, one pending applicant, and nine artworks — three per listing type, with one live auction carrying a bid history.

## Decisions needed from the product owner

§8 of the PRD lists six. The two that block schema work:

1. **Payment provider** — Razorpay for INR. Determines the `payments` columns and webhook shape (§3.7, §4.5).
2. **Commission and payouts in v1?** — `orders.platform_fee` / `artist_payout` exist but are unused if payouts are out of scope.
