# Staging and dev databases

## Seeding a dev/staging database

`scripts/seed_dev.py` fills a database with realistic demo content: 1 admin, 1 demo collector,
6 approved artists (3 featured), 2 pending applicants (with 3 sample works each) and 30 artworks
(13 sale, 9 auction, 8 display; 2 of the sale works are sold) with 1-3 images each. Prices are INR.
Images are public-domain paintings on Wikimedia Commons, used as placeholders; titles and
attributions are fictional. Auctions carry a starting price only (there is no bids table yet).

```
ALLOW_SEED=1 \
SEED_ADMIN_EMAIL=admin@yourdomain.test \
SEED_ADMIN_PASSWORD=... \
SEED_USER_PASSWORD=... \
python scripts/seed_dev.py
```

Point `DATABASE_URL` (or the `POSTGRES_*` vars) at the target database and run migrations first
(`alembic upgrade head`).

| Variable | Purpose |
| --- | --- |
| `ALLOW_SEED=1` | Required. Explicit confirmation. |
| `ENVIRONMENT` | Must be `development`, `dev`, `local`, `staging` or `test`. Anything else, including `production`, is refused. |
| `SEED_ADMIN_EMAIL`, `SEED_ADMIN_PASSWORD` | Admin account. Required, never stored in the repo. |
| `SEED_USER_PASSWORD` | Shared password for the demo collector, artists and applicants. Required. |

Demo accounts use `@seed.picasso.test` addresses (collector: `demo.collector@seed.picasso.test`).

Re-running is safe: users are matched by email, artist profiles by user, artworks by slug, and rows
are updated in place, so there are no duplicates. A changed password env var is re-applied.

Safety: the script fails closed. Set `ENVIRONMENT=production` on the production service (the app
default is `development`, so an unset value is not detected as production) and never run the script
with production's `DATABASE_URL`.
