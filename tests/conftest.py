import asyncio
import io
import os

os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@localhost/db")

import pytest
from PIL import Image
from sqlalchemy import text
from sqlalchemy.pool import NullPool
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa: F401  (register all tables)
from app.db.session import Base
from app.models.artist_profile import ArtistProfile
from app.models.artwork import Artwork, ArtworkStatus, ListingType
from app.models.artwork_image import ArtworkImage


class DbHarness:
    """SQLite stand-in for Postgres (no Postgres is reachable in dev). Only the tables the
    image/artwork code touches are created; the partial unique index on is_primary is recreated
    as a SQLite partial index so the rule is still enforced."""

    def __init__(self, path):
        self.loop = asyncio.new_event_loop()
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{path}", poolclass=NullPool)
        self.maker = async_sessionmaker(self.engine, expire_on_commit=False, class_=AsyncSession)
        tables = [ArtistProfile.__table__, Artwork.__table__, ArtworkImage.__table__]

        async def setup():
            async with self.engine.begin() as conn:
                await conn.run_sync(
                    lambda c: Base.metadata.create_all(
                        c,
                        tables=[
                            t for t in tables
                        ],
                    )
                )

        for idx in ArtworkImage.__table__.indexes:
            idx.dialect_options["sqlite"]["where"] = text("is_primary")
        self.run(setup())

    def run(self, coro):
        return self.loop.run_until_complete(coro)

    def session(self):
        return self.maker()

    def close(self):
        self.run(self.engine.dispose())
        self.loop.close()

    def make_artist(self, name="Artist One"):
        import uuid

        async def go():
            async with self.session() as s:
                a = ArtistProfile(
                    user_id=uuid.uuid4(), display_name=name, slug=name.lower().replace(" ", "-"),
                    primary_medium="Oil",
                )
                s.add(a)
                await s.commit()
                return a.id

        return self.run(go())

    def make_artwork(self, artist_id, status=ArtworkStatus.draft, title="T", year=2020, medium="Oil",
                     slug=None):
        import uuid

        async def go():
            async with self.session() as s:
                a = Artwork(
                    artist_id=artist_id, title=title, slug=slug or f"s-{uuid.uuid4().hex[:8]}",
                    year=year, medium=medium, status=status, listing_type=ListingType.display,
                )
                s.add(a)
                await s.commit()
                return a.id

        return self.run(go())


@pytest.fixture
def harness(tmp_path):
    h = DbHarness(tmp_path / "t.db")
    yield h
    h.close()


def make_image_bytes(fmt="JPEG", size=(300, 200), exif=False, mode="RGB", color=(200, 30, 30)):
    img = Image.new(mode, size, color if mode == "RGB" else (200, 30, 30, 128))
    buf = io.BytesIO()
    kwargs = {}
    if exif:
        e = Image.Exif()
        e[0x010F] = "SecretCameraMaker"
        e[0x8825] = {1: "N", 2: (12.0, 0.0, 0.0)}  # GPS IFD
        kwargs["exif"] = e
    img.save(buf, format=fmt, **kwargs)
    return buf.getvalue()


@pytest.fixture
def image_bytes():
    return make_image_bytes
