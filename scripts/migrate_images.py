"""Move artwork images between storage providers. Procedure: docs/STORAGE.md.

    ALLOW_MIGRATE=1 python scripts/migrate_images.py --from r2 --to cloudinary --dry-run
    ALLOW_MIGRATE=1 python scripts/migrate_images.py --from r2 --to cloudinary
    ALLOW_MIGRATE=1 python scripts/migrate_images.py --from r2 --to cloudinary --delete-source

Both providers' environment variables must be set (the app's normal ones: R2_*, CLOUDINARY_*,
S3_*). Run it against the database of the environment whose ENVIRONMENT value you set; only
objects under that environment's key prefix are touched. Safe to re-run.
"""
import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PROVIDER_CHOICES = ("cloudinary", "r2", "s3")


class MigrateRefused(Exception):
    pass


def check_guard(env) -> None:
    """Raise MigrateRefused unless explicitly confirmed. Pure: takes the env mapping."""
    if env.get("ALLOW_MIGRATE") != "1":
        raise MigrateRefused(
            "Refusing to migrate: set ALLOW_MIGRATE=1 to confirm you are pointed at the intended "
            "database and have a backup."
        )


def parse_args(argv):
    p = argparse.ArgumentParser(description="Copy artwork images between storage providers.")
    p.add_argument("--from", dest="source", required=True, choices=PROVIDER_CHOICES)
    p.add_argument("--to", dest="dest", required=True, choices=PROVIDER_CHOICES)
    p.add_argument("--dry-run", action="store_true", help="report only; change nothing")
    p.add_argument(
        "--delete-source",
        action="store_true",
        help="also delete the old objects (only those whose copy is verified on the destination)",
    )
    args = p.parse_args(argv)
    if args.source == args.dest:
        p.error("--from and --to must differ")
    return args


async def run(args, source, dest, session_maker, fetch=None) -> int:
    from app.services.image_migration import MigrationRefused, default_fetcher, migrate_images

    async with session_maker() as db:
        try:
            report = await migrate_images(
                db,
                source,
                dest,
                dry_run=args.dry_run,
                delete_source=args.delete_source,
                fetch=fetch or default_fetcher,
            )
        except MigrationRefused as exc:
            print(f"Refusing: {exc}", file=sys.stderr)
            return 2
    print(f"{args.source} -> {args.dest}")
    print("\n".join(report.lines()))
    return 1 if report.errors else 0


def main(argv=None) -> int:
    try:
        check_guard(os.environ)
    except MigrateRefused as exc:
        print(str(exc), file=sys.stderr)
        return 2
    args = parse_args(argv)

    from app.core.config import settings
    from app.db.session import AsyncSessionLocal
    from app.services.storage import build_storage

    print(f"ENVIRONMENT={settings.ENVIRONMENT!r} (only keys under this prefix are touched)")
    return asyncio.run(
        run(args, build_storage(args.source), build_storage(args.dest), AsyncSessionLocal)
    )


if __name__ == "__main__":
    sys.exit(main())
