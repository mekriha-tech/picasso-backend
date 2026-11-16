import uuid

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_current_admin_user
from app.db.session import get_db
from app.main import app
from app.models.artwork import ArtworkStatus
from app.services.storage import UnconfiguredStorage, get_image_storage
from tests.conftest import make_image_bytes
from tests.fakes import InMemoryStorage


@pytest.fixture
def api(harness):
    storage = InMemoryStorage()

    async def override_db():
        async with harness.session() as s:
            yield s

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_admin_user] = lambda: object()
    app.dependency_overrides[get_image_storage] = lambda: storage
    client = TestClient(app)
    client.storage = storage
    client.h = harness
    yield client
    app.dependency_overrides.clear()


def post_image(api, aid, data=None, name="x.jpg", ctype="image/jpeg", **form):
    data = data if data is not None else make_image_bytes()
    return api.post(
        f"/api/v1/admin/artworks/{aid}/images", files={"file": (name, data, ctype)}, data=form
    )


def new_artwork(api, status=ArtworkStatus.draft):
    return api.h.make_artwork(api.h.make_artist(f"A {uuid.uuid4().hex[:6]}"), status=status)


def test_upload_returns_201_with_urls(api):
    aid = new_artwork(api)
    r = post_image(api, aid, name="../../evil name.php", alt_text="A sunset")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["is_primary"] and body["alt_text"] == "A sunset"
    assert body["url"].startswith("https://cdn.example.test/staging/artworks/")
    assert "evil" not in body["url"] and "php" not in body["url"]
    assert body["preview_url"] != body["url"]


def test_upload_ignores_content_type_and_validates_content(api):
    aid = new_artwork(api)
    r = post_image(api, aid, data=b"<?php echo 1; ?>", name="a.jpg", ctype="image/jpeg")
    assert r.status_code == 422
    assert r.json() == {"detail": "That file is not a valid JPEG, PNG or WebP image."}
    # a genuine image labelled as something else is still accepted
    assert post_image(api, aid, ctype="text/plain").status_code == 201


def test_upload_too_large_is_413(api, monkeypatch):
    from app.services import image_processing

    monkeypatch.setattr(image_processing, "MAX_UPLOAD_BYTES", 100)
    r = post_image(api, new_artwork(api), data=make_image_bytes(size=(400, 400)))
    assert r.status_code == 413


def test_upload_404_for_unknown_artwork(api):
    assert post_image(api, uuid.uuid4()).status_code == 404


def test_upload_503_when_storage_not_configured(api):
    app.dependency_overrides[get_image_storage] = lambda: UnconfiguredStorage()
    r = post_image(api, new_artwork(api))
    assert r.status_code == 503
    assert r.json() == {"detail": "Image storage is not configured."}


def test_list_order_primary_and_delete_flow(api):
    aid = new_artwork(api, ArtworkStatus.published)
    ids = [post_image(api, aid).json()["id"] for _ in range(2)]
    base = f"/api/v1/admin/artworks/{aid}/images"

    listed = api.get(base).json()
    assert [i["id"] for i in listed] == ids

    r = api.put(f"{base}/order", json={"image_ids": ids[::-1]})
    assert r.status_code == 200 and [i["id"] for i in r.json()] == ids[::-1]
    assert api.put(f"{base}/order", json={"image_ids": ids[:1]}).status_code == 422

    r = api.post(f"{base}/{ids[1]}/primary")
    assert r.status_code == 200
    assert [i["id"] for i in r.json() if i["is_primary"]] == [ids[1]]

    assert api.delete(f"{base}/{ids[0]}").status_code == 204
    last = api.delete(f"{base}/{ids[1]}")
    assert last.status_code == 409  # rule 10: published artwork keeps >=1 image
    assert api.delete(f"{base}/{uuid.uuid4()}").status_code == 404
    assert len(api.storage.objects) == 2


def test_endpoints_require_admin_auth():
    app.dependency_overrides.clear()
    c = TestClient(app)
    aid = uuid.uuid4()
    assert c.get(f"/api/v1/admin/artworks/{aid}/images").status_code == 401
    assert c.post(f"/api/v1/admin/artworks/{aid}/images", files={"file": ("a", b"x")}).status_code == 401
    assert c.delete(f"/api/v1/admin/artworks/{aid}/images/{uuid.uuid4()}").status_code == 401
