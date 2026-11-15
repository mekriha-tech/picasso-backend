"""Seed a dev/staging database with realistic demo content.

    ALLOW_SEED=1 SEED_ADMIN_EMAIL=... SEED_ADMIN_PASSWORD=... SEED_USER_PASSWORD=... \
        python scripts/seed_dev.py

Safe to re-run: users are upserted by email, artist profiles by user, artworks by slug.
Refuses to run unless ALLOW_SEED=1 and ENVIRONMENT is a known non-production value.
Passwords come from the environment only; nothing secret lives in this repo.

Artworks go through the same rules as the app: rows satisfy the schema CHECK constraints,
and anything published is checked against PRD rule 10 (title, year, medium, one image).
"""
import asyncio
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

# ENVIRONMENT values we are willing to seed. Anything else (including "production")
# is refused, so a typo or new environment name fails closed.
SEEDABLE_ENVIRONMENTS = {"development", "dev", "local", "staging", "test"}

REQUIRED_SECRETS = ("SEED_ADMIN_EMAIL", "SEED_ADMIN_PASSWORD", "SEED_USER_PASSWORD")


class SeedRefused(Exception):
    pass


def check_guard(env: dict[str, str]) -> None:
    """Raise SeedRefused unless it is explicitly safe to seed. Pure: takes the env mapping."""
    if env.get("ALLOW_SEED") != "1":
        raise SeedRefused("Refusing to seed: set ALLOW_SEED=1 to confirm this is a dev/staging database.")
    environment = (env.get("ENVIRONMENT") or "development").strip().lower()
    if environment not in SEEDABLE_ENVIRONMENTS:
        raise SeedRefused(
            f"Refusing to seed: ENVIRONMENT={environment!r} is not one of "
            f"{sorted(SEEDABLE_ENVIRONMENTS)}. Never seed production."
        )
    missing = [name for name in REQUIRED_SECRETS if not env.get(name)]
    if missing:
        raise SeedRefused(f"Refusing to seed: set {', '.join(missing)} in the environment.")


@dataclass
class ArtworkRow:
    """Everything an artwork row needs, validated before touching the database."""
    slug: str
    artist_slug: str
    title: str
    year: int
    medium: str
    dimensions: str
    width_cm: Decimal
    height_cm: Decimal
    category: str
    listing_type: str
    status: str
    price: Decimal | None
    sold_price: Decimal | None
    image_urls: list[str]
    description: str


def build_artworks(artworks, images) -> list[ArtworkRow]:
    """Turn the compact specs into rows, handing out images from the pool in order.

    Enforces the schema rules in Python first so a bad spec fails with a clear message
    instead of a database error: sale needs a price, display has none, and published,
    reserved or sold works need title, year, medium and at least one image (PRD rule 10).
    """
    from app.services.slugs import slugify

    rows: list[ArtworkRow] = []
    cursor = 0
    for spec in artworks:
        n = spec["images"]
        urls = [images[(cursor + k) % len(images)][0] for k in range(n)]
        cursor += n
        price = Decimal(spec["price"]) if spec.get("price") is not None else None
        sold_price = Decimal(spec["sold_price"]) if spec.get("sold_price") is not None else None
        row = ArtworkRow(
            slug=slugify(spec["title"]),
            artist_slug=spec["artist"],
            title=spec["title"],
            year=spec["year"],
            medium=spec["medium"],
            dimensions=spec["dimensions"],
            width_cm=Decimal(spec["width_cm"]),
            height_cm=Decimal(spec["height_cm"]),
            category=spec["category"],
            listing_type=spec["listing_type"],
            status=spec.get("status", "published"),
            price=price,
            sold_price=sold_price,
            image_urls=urls,
            description=spec["description"],
        )
        _validate(row)
        rows.append(row)
    slugs = [r.slug for r in rows]
    if len(slugs) != len(set(slugs)):
        raise ValueError("Duplicate artwork slugs in seed data.")
    return rows


def _validate(row: ArtworkRow) -> None:
    if row.listing_type == "sale" and row.price is None:
        raise ValueError(f"{row.title}: sale listings need a price.")
    if row.listing_type == "display" and row.price is not None:
        raise ValueError(f"{row.title}: display listings must not have a price.")
    if row.status in ("published", "reserved", "sold"):
        if not row.title or not row.year or not row.medium:
            raise ValueError(f"{row.title}: needs a title, year, and medium before it can be published.")
        if not row.image_urls:
            raise ValueError(f"{row.title}: needs at least one image before it can be published.")
    if row.status == "sold" and row.sold_price is None:
        raise ValueError(f"{row.title}: sold works need a sold_price.")


async def _upsert_user(db, *, email, password, full_name, location, is_admin=False, artist_status="none"):
    from sqlalchemy import select
    from app.core.security import get_password_hash, verify_password
    from app.models.user import ArtistStatus, User

    user = (await db.execute(select(User).where(User.email == email))).scalars().first()
    created = user is None
    if created:
        user = User(email=email)
        db.add(user)
    user.full_name = full_name
    user.location = location
    user.is_admin = is_admin
    user.artist_status = ArtistStatus(artist_status)
    user.email_verified_at = user.email_verified_at or datetime.now(timezone.utc)
    # Only re-hash (slow) when the password is new or was changed in the environment.
    if not user.password_hash or not verify_password(password, user.password_hash):
        user.password_hash = get_password_hash(password)
    await db.flush()
    return user, created


async def seed(db, env: dict[str, str]) -> dict[str, int]:
    from sqlalchemy import delete, select
    from app.models.application_work import ApplicationWork
    from app.models.artist_application import ApplicationStatus, ArtistApplication
    from app.models.artist_profile import ArtistProfile
    from app.models.artwork import Artwork, ArtworkStatus, ListingType
    from app.models.artwork_image import ArtworkImage
    from app.services.slugs import slugify
    import seed_dev_data as data

    counts = {"users_created": 0, "users_updated": 0, "artists": 0, "applicants": 0,
              "artworks_created": 0, "artworks_updated": 0, "images": 0}
    now = datetime.now(timezone.utc)

    def tally(created: bool):
        counts["users_created" if created else "users_updated"] += 1

    _, created = await _upsert_user(
        db, email=env["SEED_ADMIN_EMAIL"], password=env["SEED_ADMIN_PASSWORD"],
        full_name="Picasso Admin", location="Mumbai, India", is_admin=True,
    )
    tally(created)
    _, created = await _upsert_user(
        db, email=data.DEMO_USER_EMAIL, password=env["SEED_USER_PASSWORD"],
        full_name="Demo Collector", location="Bengaluru, India",
    )
    tally(created)

    profiles: dict[str, ArtistProfile] = {}
    for a in data.ARTISTS:
        user, created = await _upsert_user(
            db, email=a["email"], password=env["SEED_USER_PASSWORD"], full_name=a["full_name"],
            location=a["location"], artist_status="approved",
        )
        tally(created)
        profile = (await db.execute(select(ArtistProfile).where(ArtistProfile.user_id == user.id))).scalars().first()
        if profile is None:
            profile = ArtistProfile(user_id=user.id, approved_at=now - timedelta(days=60))
            db.add(profile)
        profile.display_name = a["full_name"]
        profile.slug = a["slug"]
        profile.primary_medium = a["primary_medium"]
        profile.years_practising = a["years_practising"]
        profile.statement = a["statement"]
        profile.website_url = a.get("website_url")
        profile.instagram = a.get("instagram")
        profile.cover_image_url = a.get("cover_image_url")
        profile.is_featured = a["featured"]
        await db.flush()
        profiles[a["slug"]] = profile
        counts["artists"] += 1

    for p in data.PENDING_APPLICANTS:
        user, created = await _upsert_user(
            db, email=p["email"], password=env["SEED_USER_PASSWORD"], full_name=p["full_name"],
            location=p["location"], artist_status="pending",
        )
        tally(created)
        application = (await db.execute(
            select(ArtistApplication).where(
                ArtistApplication.user_id == user.id,
                ArtistApplication.status == ApplicationStatus.submitted,
            )
        )).scalars().first()
        if application is None:
            application = ArtistApplication(user_id=user.id, status=ApplicationStatus.submitted,
                                            submitted_at=now - timedelta(days=2))
            db.add(application)
        application.full_name = p["full_name"]
        application.location = p["location"]
        application.primary_medium = p["primary_medium"]
        application.years_practising = p["years_practising"]
        application.statement = p["statement"]
        application.instagram = p.get("instagram")
        await db.flush()
        await db.execute(delete(ApplicationWork).where(ApplicationWork.application_id == application.id))
        for slot, work in enumerate(p["works"]):
            db.add(ApplicationWork(application_id=application.id, slot_index=slot, **work))
        counts["applicants"] += 1

    rows = build_artworks(data.ARTWORKS, data.IMAGES)
    for i, row in enumerate(rows):
        artist = profiles[row.artist_slug]
        artwork = (await db.execute(select(Artwork).where(Artwork.slug == row.slug))).scalars().first()
        if artwork is None:
            artwork = Artwork(slug=row.slug, artist_id=artist.id)
            artwork.created_at = now - timedelta(days=45, hours=-i * 20)
            artwork.published_at = artwork.created_at + timedelta(hours=2)
            db.add(artwork)
            counts["artworks_created"] += 1
        else:
            counts["artworks_updated"] += 1
        artwork.artist_id = artist.id
        artwork.title = row.title
        artwork.description = row.description
        artwork.year = row.year
        artwork.medium = row.medium
        artwork.dimensions = row.dimensions
        artwork.width_cm = row.width_cm
        artwork.height_cm = row.height_cm
        artwork.category = row.category
        artwork.listing_type = ListingType(row.listing_type)
        artwork.status = ArtworkStatus(row.status)
        artwork.price = row.price
        artwork.sold = row.status == "sold"
        artwork.sold_price = row.sold_price
        artwork.sold_at = (now - timedelta(days=5)) if row.status == "sold" else None
        artwork.primary_image_url = row.image_urls[0]
        await db.flush()
        await db.execute(delete(ArtworkImage).where(ArtworkImage.artwork_id == artwork.id))
        for k, url in enumerate(row.image_urls):
            db.add(ArtworkImage(artwork_id=artwork.id, url=url, alt_text=f"{row.title} ({k + 1})",
                                sort_order=k, is_primary=(k == 0)))
            counts["images"] += 1
        await db.flush()

    await db.commit()
    return counts


async def main() -> int:
    from app.core.config import settings

    # Use the app's own resolved ENVIRONMENT (env var or .env), not just the shell's.
    env = {**os.environ, "ENVIRONMENT": settings.ENVIRONMENT}
    try:
        check_guard(env)
    except SeedRefused as exc:
        print(exc, file=sys.stderr)
        return 1

    from app.db.session import AsyncSessionLocal, engine

    try:
        async with AsyncSessionLocal() as db:
            counts = await seed(db, dict(os.environ))
    finally:
        await engine.dispose()

    print("Seed complete:")
    for key, value in counts.items():
        print(f"  {key}: {value}")
    print(f"Admin login: {os.environ['SEED_ADMIN_EMAIL']}; demo logins use SEED_USER_PASSWORD.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
