"""Guard and data checks for scripts/seed_dev.py. No database is needed: the DB-backed
upsert is verified by running the script twice against a dev/staging database."""
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@localhost/db")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import seed_dev  # noqa: E402
import seed_dev_data as data  # noqa: E402

GOOD_ENV = {
    "ALLOW_SEED": "1",
    "ENVIRONMENT": "staging",
    "SEED_ADMIN_EMAIL": "admin@example.com",
    "SEED_ADMIN_PASSWORD": "x",
    "SEED_USER_PASSWORD": "y",
}


def test_guard_allows_staging_with_flag_and_secrets():
    seed_dev.check_guard(GOOD_ENV)


def test_guard_refuses_without_flag():
    with pytest.raises(seed_dev.SeedRefused, match="ALLOW_SEED=1"):
        seed_dev.check_guard({k: v for k, v in GOOD_ENV.items() if k != "ALLOW_SEED"})


def test_guard_refuses_flag_that_is_not_exactly_one():
    with pytest.raises(seed_dev.SeedRefused):
        seed_dev.check_guard({**GOOD_ENV, "ALLOW_SEED": "true"})


@pytest.mark.parametrize("environment", ["production", "Production", "prod", "live", "something-else"])
def test_guard_refuses_production_and_unknown_environments(environment):
    with pytest.raises(seed_dev.SeedRefused, match="Never seed production"):
        seed_dev.check_guard({**GOOD_ENV, "ENVIRONMENT": environment})


@pytest.mark.parametrize("name", seed_dev.REQUIRED_SECRETS)
def test_guard_requires_each_secret(name):
    env = {k: v for k, v in GOOD_ENV.items() if k != name}
    with pytest.raises(seed_dev.SeedRefused, match=name):
        seed_dev.check_guard(env)
    with pytest.raises(seed_dev.SeedRefused, match=name):
        seed_dev.check_guard({**GOOD_ENV, name: ""})


def test_main_refuses_without_flag(monkeypatch, capsys):
    import asyncio

    monkeypatch.delenv("ALLOW_SEED", raising=False)
    assert asyncio.run(seed_dev.main()) == 1
    assert "ALLOW_SEED=1" in capsys.readouterr().err


def test_no_passwords_in_seed_data():
    assert "password" not in repr(data.ARTISTS).lower()
    assert "password" not in repr(data.PENDING_APPLICANTS).lower()


def test_seed_set_shape():
    rows = seed_dev.build_artworks(data.ARTWORKS, data.IMAGES)
    assert len(rows) == 30
    assert {r.listing_type for r in rows} == {"sale", "auction", "display"}
    assert sum(r.status == "sold" for r in rows) == 2
    assert len(data.ARTISTS) == 6 and sum(a["featured"] for a in data.ARTISTS) >= 2
    assert len(data.PENDING_APPLICANTS) == 2
    assert all(1 <= len(r.image_urls) <= 3 for r in rows)
    assert {r.artist_slug for r in rows} == {a["slug"] for a in data.ARTISTS}


def test_seed_rows_satisfy_schema_rules():
    for r in seed_dev.build_artworks(data.ARTWORKS, data.IMAGES):
        if r.listing_type == "sale":
            assert r.price is not None
        if r.listing_type == "display":
            assert r.price is None
        assert r.title and r.year and r.medium and r.image_urls  # PRD rule 10
        assert r.price is None or r.price.as_tuple().exponent >= -2  # fits NUMERIC(12,2)


def test_build_is_deterministic_so_reruns_are_idempotent():
    a = seed_dev.build_artworks(data.ARTWORKS, data.IMAGES)
    b = seed_dev.build_artworks(data.ARTWORKS, data.IMAGES)
    assert a == b
    assert len({r.slug for r in a}) == len(a)


def test_build_rejects_sale_without_price():
    spec = dict(data.ARTWORKS[0], price=None)
    with pytest.raises(ValueError, match="need a price"):
        seed_dev.build_artworks([spec], data.IMAGES)


def test_build_rejects_display_with_price():
    spec = dict(next(s for s in data.ARTWORKS if s["listing_type"] == "display"), price="100")
    with pytest.raises(ValueError, match="must not have a price"):
        seed_dev.build_artworks([spec], data.IMAGES)


def test_build_rejects_publishing_without_image():
    spec = dict(data.ARTWORKS[0], images=0)
    with pytest.raises(ValueError, match="at least one image"):
        seed_dev.build_artworks([spec], data.IMAGES)


def test_seed_exhibitions_one_of_each_kind():
    kinds = {e["viewer_kind"]: e for e in data.EXHIBITIONS}
    assert set(kinds) == {"vendor_embed", "first_party"} and len(data.EXHIBITIONS) == 2
    assert kinds["vendor_embed"]["vr_tour_url"].startswith("https://example.com/")
    assert kinds["first_party"]["artwork_titles"]
    assert len({e["slug"] for e in data.EXHIBITIONS}) == 2


def test_seed_first_party_gallery_uses_existing_published_artworks():
    rows = {r.slug: r for r in seed_dev.build_artworks(data.ARTWORKS, data.IMAGES)}
    from app.services.slugs import slugify

    gallery = next(e for e in data.EXHIBITIONS if e["viewer_kind"] == "first_party")
    titles = gallery["artwork_titles"]
    assert len(titles) == len(set(titles))
    for title in titles:
        assert slugify(title) in rows
        assert rows[slugify(title)].status in ("published", "reserved", "sold")


def test_seed_wires_exhibitions_into_seed():
    import inspect

    assert "data.EXHIBITIONS" in inspect.getsource(seed_dev.seed)
