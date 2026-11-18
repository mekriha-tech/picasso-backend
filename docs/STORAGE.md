# Image storage and moving between providers

Artwork images live in an external object store behind one small interface
(`app/services/storage.py`). Three providers are implemented:

| Provider | `STORAGE_BACKEND` | Implementation |
| --- | --- | --- |
| Cloudinary | `cloudinary` | official `cloudinary` SDK |
| Cloudflare R2 | `r2` | boto3 S3 client, account endpoint, region `auto` |
| Any S3-compatible store (AWS S3, MinIO, ...) | `s3` | boto3 S3 client, optional endpoint/region |

## Configuration

| Variable | Meaning |
| --- | --- |
| `STORAGE_BACKEND` | `cloudinary`, `r2` or `s3`: where NEW uploads go. Unset = whichever provider is fully configured (if several are, `r2`, then `cloudinary`, then `s3`, with a warning in the log; set it explicitly). Any other value stops the app at startup. |
| `CLOUDINARY_CLOUD_NAME`, `CLOUDINARY_API_KEY`, `CLOUDINARY_API_SECRET` | All three required for Cloudinary. |
| `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET`, `R2_PUBLIC_BASE_URL` | All five required for R2. |
| `S3_BUCKET`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `S3_PUBLIC_BASE_URL` | Required for generic S3. |
| `S3_ENDPOINT_URL`, `S3_REGION` | Optional for generic S3 (endpoint for non-AWS stores, region for AWS). |
| `ENVIRONMENT` | Becomes the key/folder prefix (`staging/...`, `production/...`). |

With nothing configured the app boots normally and every upload answers `503`
`Image storage is not configured.` If `STORAGE_BACKEND` names a provider whose variables are
incomplete, that is the same 503 (it does not fall back to another provider).

## How an image is stored

- Uploads go through our own validation and processing first (`image_processing.py`: decode,
  strip EXIF/GPS, re-encode WebP). Only the processed bytes ever reach a provider.
- Two assets per image, uploaded as separate objects: the full size (`url`, `storage_key`) and the
  800px preview (`preview_url`, `preview_storage_key`). **Why two assets and not one asset plus a
  Cloudinary derivation:** the preview already exists after processing and the schema already
  has a column pair for it; two plain objects behave identically on Cloudinary, R2 and S3, so the
  migration is a straight copy and no provider-specific URL transformation (which would not exist
  on R2/S3, would have to be rebuilt on a move, and consumes Cloudinary transformation credits)
  leaks into the data. The price is a second stored object per image.
- Keys are `<environment>/artworks/<random>.webp` on every provider. On Cloudinary the public_id
  is the key without `.webp` (so the folder and environment prefix are kept) and the delivery URL
  is `https://res.cloudinary.com/<cloud>/image/upload/<key>`. URLs are built from provider + key
  when the image is written and stored on the row; nothing builds URLs at read time.
- Each `artwork_images` row records its own `storage_provider` (`r2`, `cloudinary`, `s3`, or
  NULL for external URLs such as the seeded Wikimedia images). Deleting an image uses the row's
  provider, so images uploaded before a switch of `STORAGE_BACKEND` still delete correctly, as
  long as that provider's variables are still set. If they are not, the delete answers 503 and the
  row is kept. External URLs and other environments' objects are never deleted.

## Moving all images to another provider

`scripts/migrate_images.py` copies images from one provider to another. Rows only change after both
of their objects were copied; it is safe to interrupt and re-run; the old objects stay until you
explicitly ask for them to be removed. Keys do not change, only `storage_provider`, `url` and
`preview_url` (and `artworks.primary_image_url` where it mirrors the primary image).

Procedure, per environment (staging first):

1. **Back up the database** (the script changes URLs). Note the row counts you expect.
2. Set the variables of BOTH providers on the machine running the script (the usual
   `R2_*` / `CLOUDINARY_*` / `S3_*`), the target environment's `DATABASE_URL`, and the right
   `ENVIRONMENT` (only keys under that prefix are touched).
3. Dry run, which reports and changes nothing (no downloads either):
   ```
   ALLOW_MIGRATE=1 python scripts/migrate_images.py --from r2 --to cloudinary --dry-run
   ```
   Check `images on source provider`, how many are `skipped` (external URLs are expected) and that
   `errors` is 0.
4. Run it for real: `ALLOW_MIGRATE=1 python scripts/migrate_images.py --from r2 --to cloudinary`.
   Exit code 0 means no errors; if something failed it lists the image ids; fix the cause and run
   the same command again, it picks up only what is left.
5. Open a few artworks in the site and the admin panel and confirm the images load from the new
   host. Note: the frontend must allow the new image host (e.g. Next.js `remotePatterns`/CSP).
6. Switch new uploads: set `STORAGE_BACKEND=<new provider>` on the service and redeploy. (Doing this
   before step 4 is also fine: old rows keep working and delete via their own provider.)
7. Later, when you are sure and the old provider's CDN caches no longer matter, reclaim the space:
   ```
   ALLOW_MIGRATE=1 python scripts/migrate_images.py --from r2 --to cloudinary --delete-source
   ```
   For every row now on the destination, this fetches the copy back from the destination's public
   URL and only then deletes the source object (missing objects are fine). Run with `--dry-run`
   first to see the count. Do not remove the old provider's credentials until this is done.

Safeguards: refuses without `ALLOW_MIGRATE=1`; refuses if source and destination are the same
store (including R2 and `s3` pointing at the same bucket); rows with no provider (external URLs)
are skipped; objects not under this environment's prefix or whose URL is not the source's own
public URL are skipped and counted.

Moving to S3/R2 from Cloudinary is the same command with `--from cloudinary --to r2` (or `s3`).
Cloudinary images are downloaded from their public delivery URL.

## Not verified against real services

The Cloudinary calls, the R2/S3 calls and the migration have been exercised with fakes only. Do
the first real run on staging, with a dry run first.
