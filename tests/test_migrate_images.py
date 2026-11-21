"""Image migration between providers: fakes only (in-memory stores, in-memory fetcher, SQLite)."""
import importlib.util
import sys
from pathlib import Path

import pytest

from app.models.artwork_image import ArtworkImage
from app.services.image_migration import Fetched, MigrationRefused, migrate_images
from app.services.storage import StorageError
from tests.fakes import InMemoryStorage
from tests.test_artwork_images_service import artwork, images, setup_artwork, upload

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "migrate_images.py"


def load_script():
    spec = importlib.util.spec_from_file_location("migrate_images_script", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["migrate_images_script"] = mod
    spec.loader.exec_module(mod)
    return mod


class World:
    def __init__(self):
        self.r2 = InMemoryStorage(provider="r2", base="https://r2.test")
        self.cl = InMemoryStorage(provider="cloudinary", base="https://res.cl.test")
        self.fetched: list[str] = []

    def fetch(self, url):
        self.fetched.append(url)
        for st in (self.r2, self.cl):
            if url.startswith(st.base + "/"):
                key = url[len(st.base) + 1:]
                if key in st.objects:
                    data, ct = st.objects[key]
                    return Fetched(data, ct)
        raise StorageError(f"404 {url}")


@pytest.fixture
def world():
    return World()


def run(h, world, **kw):
    async def go():
        async with h.session() as s:
            return await migrate_images(s, world.r2, world.cl, fetch=world.fetch, **kw)

    return h.run(go())


def seed_external(h, aid):
    async def go():
        async with h.session() as s:
            s.add(ArtworkImage(artwork_id=aid, url="https://upload.wikimedia.org/x.jpg", sort_order=9))
            await s.commit()

    h.run(go())


def test_dry_run_reports_and_changes_nothing(harness, world):
    aid = setup_artwork(harness)
    upload(harness, world.r2, aid)
    upload(harness, world.r2, aid)
    seed_external(harness, aid)
    before = [(i.url, i.storage_provider) for i in images(harness, aid)]

    r = run(harness, world, dry_run=True)
    assert (r.images_scanned, r.images_migrated, r.objects_copied, r.skipped_external) == (2, 2, 4, 1)
    assert world.cl.objects == {} and world.fetched == []
    assert [(i.url, i.storage_provider) for i in images(harness, aid)] == before
    assert any("DRY RUN" in line for line in r.lines())


def test_migrates_rows_primary_url_and_keeps_source(harness, world):
    aid = setup_artwork(harness)
    a = upload(harness, world.r2, aid)
    b = upload(harness, world.r2, aid)
    seed_external(harness, aid)

    r = run(harness, world)
    assert (r.images_migrated, r.objects_copied, r.errors) == (2, 4, [])
    rows = {i.id: i for i in images(harness, aid)}
    for old in (a, b):
        row = rows[old.id]
        assert row.storage_provider == "cloudinary"
        assert row.storage_key == old.storage_key  # key (with env prefix) preserved
        assert row.url == world.cl.public_url(old.storage_key)
        assert row.preview_url == world.cl.public_url(old.preview_storage_key)
        assert world.cl.objects[old.storage_key][0] == world.r2.objects[old.storage_key][0]
    # the artwork's mirrored primary URL follows the primary image
    assert artwork(harness, aid).primary_image_url == rows[a.id].url
    # nothing deleted from the source without --delete-source
    assert world.r2.deleted == [] and len(world.r2.objects) == 4
    # external row untouched
    ext = next(i for i in rows.values() if i.storage_provider is None)
    assert ext.url == "https://upload.wikimedia.org/x.jpg"


def test_rerun_is_idempotent(harness, world):
    aid = setup_artwork(harness)
    upload(harness, world.r2, aid)
    run(harness, world)
    puts = dict(world.cl.objects)
    r = run(harness, world)
    assert (r.images_scanned, r.images_migrated, r.objects_copied) == (0, 0, 0)
    assert r.already_on_destination == 1
    assert world.cl.objects == puts


def test_failure_leaves_row_on_source_and_rerun_resumes(harness, world):
    aid = setup_artwork(harness)
    a = upload(harness, world.r2, aid)
    b = upload(harness, world.r2, aid)
    del world.r2.objects[b.preview_storage_key]  # b cannot be fetched

    r = run(harness, world)
    assert r.images_migrated == 1 and len(r.errors) == 1
    rows = {i.id: i for i in images(harness, aid)}
    assert rows[a.id].storage_provider == "cloudinary"
    assert rows[b.id].storage_provider == "r2" and rows[b.id].url == b.url

    world.r2.objects[b.preview_storage_key] = (b"x", "image/webp")  # source healed
    r = run(harness, world)
    assert r.images_migrated == 1 and r.errors == []
    assert all(i.storage_provider == "cloudinary" for i in images(harness, aid))


def test_destination_write_failure_does_not_update_row(harness, world):
    aid = setup_artwork(harness)
    upload(harness, world.r2, aid)
    world.cl.fail_put_after = 0
    r = run(harness, world)
    assert r.images_migrated == 0 and len(r.errors) == 1
    assert images(harness, aid)[0].storage_provider == "r2"


def test_skips_objects_of_other_environments(harness, world):
    aid = setup_artwork(harness)

    async def go():
        async with harness.session() as s:
            s.add(ArtworkImage(
                artwork_id=aid, url=world.r2.public_url("production/artworks/z.webp"),
                storage_provider="r2", storage_key="production/artworks/z.webp", sort_order=0,
            ))
            await s.commit()

    harness.run(go())
    r = run(harness, world)
    assert r.skipped_not_owned == 1 and r.images_migrated == 0 and world.fetched == []


def test_delete_source_is_separate_and_verified(harness, world):
    aid = setup_artwork(harness)
    a = upload(harness, world.r2, aid)
    run(harness, world)  # first run: copy only
    assert len(world.r2.objects) == 2

    r = run(harness, world, delete_source=True, dry_run=True)
    assert r.source_objects_deleted == 2 and len(world.r2.objects) == 2

    r = run(harness, world, delete_source=True)  # row already migrated by the earlier run
    assert r.source_objects_deleted == 2 and r.errors == []
    assert world.r2.objects == {}
    assert a.storage_key in world.cl.objects  # destination copy intact


def test_delete_source_keeps_source_if_destination_copy_unreadable(harness, world):
    aid = setup_artwork(harness)
    a = upload(harness, world.r2, aid)
    run(harness, world)
    del world.cl.objects[a.preview_storage_key]
    r = run(harness, world, delete_source=True)
    assert r.source_objects_deleted == 1 and len(r.errors) == 1
    assert a.preview_storage_key in world.r2.objects


def test_refuses_same_store_and_unconfigured(harness, world):
    same = InMemoryStorage(provider="s3", base="https://r2.test")
    twin = InMemoryStorage(provider="r2", base="https://r2.test")

    async def go(src, dst):
        async with harness.session() as s:
            return await migrate_images(s, src, dst, fetch=world.fetch)

    with pytest.raises(MigrationRefused):
        harness.run(go(twin, InMemoryStorage(provider="r2", base="https://other")))
    with pytest.raises(MigrationRefused):
        harness.run(go(twin, same))  # distinct providers but the same physical store
    from app.services.storage import UnconfiguredStorage

    with pytest.raises(MigrationRefused):
        harness.run(go(twin, UnconfiguredStorage()))


def test_script_guard_and_args():
    mod = load_script()
    with pytest.raises(mod.MigrateRefused):
        mod.check_guard({})
    with pytest.raises(mod.MigrateRefused):
        mod.check_guard({"ALLOW_MIGRATE": "yes"})
    mod.check_guard({"ALLOW_MIGRATE": "1"})
    a = mod.parse_args(["--from", "r2", "--to", "cloudinary", "--dry-run"])
    assert (a.source, a.dest, a.dry_run, a.delete_source) == ("r2", "cloudinary", True, False)
    with pytest.raises(SystemExit):
        mod.parse_args(["--from", "r2", "--to", "r2"])
    assert mod.main(["--from", "r2", "--to", "s3"]) == 2  # no ALLOW_MIGRATE in the test env


def test_script_run_prints_report_and_exit_code(harness, world, capsys):
    mod = load_script()
    aid = setup_artwork(harness)
    upload(harness, world.r2, aid)
    args = mod.parse_args(["--from", "r2", "--to", "cloudinary", "--dry-run"])
    code = harness.run(mod.run(args, world.r2, world.cl, harness.maker, fetch=world.fetch))
    out = capsys.readouterr().out
    assert code == 0 and "r2 -> cloudinary" in out and "DRY RUN" in out
