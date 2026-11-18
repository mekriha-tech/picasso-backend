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

## Image storage (Cloudinary, R2 or S3)

Providers, the per-image `storage_provider` column and the procedure for moving images from one
provider to another (`scripts/migrate_images.py`, guarded by `ALLOW_MIGRATE=1`) are in
[STORAGE.md](STORAGE.md). Run `alembic upgrade head` first (adds `artwork_images.storage_provider`
and marks existing R2 images `r2`).

Pick the provider for new uploads with `STORAGE_BACKEND=cloudinary|r2|s3` (unset = whichever is
configured; set it explicitly when more than one is). All storage variables are optional and the
app boots without them; uploads return 503 until the chosen provider is fully configured.

For Cloudinary set `CLOUDINARY_CLOUD_NAME`, `CLOUDINARY_API_KEY`, `CLOUDINARY_API_SECRET`. Generic S3
uses `S3_BUCKET`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `S3_PUBLIC_BASE_URL` and optionally
`S3_ENDPOINT_URL`, `S3_REGION`. The frontend must allow the image host of whichever provider is
used (for Cloudinary: `res.cloudinary.com`).

### Cloudflare R2 variables

Set these for R2 (all five are required):

| Variable | Purpose |
| --- | --- |
| `R2_ACCOUNT_ID` | Cloudflare account id (builds the S3 endpoint). |
| `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` | R2 API token, scoped to the bucket with object read/write. |
| `R2_BUCKET` | Bucket name. |
| `R2_PUBLIC_BASE_URL` | Public base URL of the bucket (r2.dev or custom domain), no trailing slash needed. |

**Production and staging may share one bucket or Cloudinary account.** To keep them apart, every object key is
prefixed with the service's `ENVIRONMENT` value, lowercased:
`production/artworks/<random>.webp` and `staging/artworks/<random>.webp` (full size and preview
each get their own random key). Set `ENVIRONMENT` correctly on each service; splitting the
environments into separate buckets later is a matter of moving one prefix.

Deleting an image only removes objects whose key is under this environment's own prefix and whose
stored URL is exactly that key under the provider's public base URL. External URLs (e.g. the Wikimedia seed
images) and the other environment's objects are never touched, and a missing object is not an
error.
