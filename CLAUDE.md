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
