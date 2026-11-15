# Backend <-> frontend collaboration

How the two repos move in parallel without stepping on each other. This is the canonical copy;
`picasso-frontend` links here.

- Backend: https://github.com/mekriha-tech/picasso-backend (FastAPI, Railway)
- Frontend: https://github.com/mekriha-tech/picasso-frontend (Next.js, Netlify)
- Product owner: makes every product decision and merges every PR.

## Where each kind of truth lives

| Truth | Home |
| --- | --- |
| What the API does | `docs/openapi.json` (reviewed contract, on `main`) and `docs/API_REFERENCE.md` (prose, quirks, known gaps). The live deployment serves the same thing at `/api/v1/openapi.json`. |
| What is being asked of the other side | GitHub Issues, filed in the repo that has to do the work |
| What was decided | A `needs-decision` issue closed with the answer in its last comment |
| How each repo works | `CLAUDE.md` and `.claude/agents/` in that repo |

Chat between sessions is only for nudges ("see issue #12"). A requirement that lives only in a
message does not exist.

## Labels (same in both repos)

- `needs-backend` / `needs-frontend` - work for that side
- `needs-decision` - blocked on the product owner; the issue lists options and a recommendation
- `contract-change` - changes or removes something already documented in the API
- `blocked` - waiting on another issue; link it

## The loop

1. Frontend needs something the API lacks: file an **API request** issue in the backend repo
   (template provided) with the screen, the fields needed and an example response.
2. Backend replies in the issue with a proposed shape. If it is not obvious, the product owner
   approves it there.
3. Backend implements. The PR says `Closes #N`, includes the regenerated `docs/openapi.json`, and
   updates `docs/API_REFERENCE.md`.
4. After merge and deploy, backend comments with the endpoint and unblocks the frontend issue.

## Rules

- **Additive changes are free. Breaking changes are not.** Never change or remove a documented
  path, method, status code or field without a `contract-change` issue the frontend has
  acknowledged.
- **The contract file cannot silently go stale:** `tests/test_openapi_contract.py` fails when
  `docs/openapi.json` differs from what the app actually serves. Regenerate with
  `python scripts/export_openapi.py`.
- **Neither side edits the other's repo.** File an issue.
- **Product questions stop work.** File a `needs-decision` issue; do not guess.
- **Production data is not scratch space.** No writes to the production database without the
  product owner's explicit approval.

## Definition of done

- Backend: tests pass, `docs/openapi.json` regenerated, `API_REFERENCE.md` updated, PR merged,
  deployment healthy, DB-touching changes smoke-tested live.
- Frontend: works against the live API (or the documented contract), handles the documented error
  shapes, and no hard-coded copies of API data.
