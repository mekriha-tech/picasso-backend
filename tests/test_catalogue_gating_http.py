"""HTTP-level checks of the logged-out teaser rules.

Every request here is rejected before any database query runs, so no database is needed.
The DB-backed side (which works fall inside the teaser) is verified against the live
deployment, as with the rest of the catalogue.
"""
import os

os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@localhost/db")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.services.teaser import LOGIN_REQUIRED_MESSAGE  # noqa: E402

client = TestClient(app)


def _assert_login_required(response):
    assert response.status_code == 401
    assert response.json() == {"detail": LOGIN_REQUIRED_MESSAGE}
    assert response.headers["www-authenticate"] == "Bearer"


def test_anonymous_cursor_requires_login():
    _assert_login_required(client.get("/api/v1/artworks", params={"cursor": "anything"}))


def test_anonymous_search_requires_login():
    _assert_login_required(client.get("/api/v1/artworks", params={"q": "sunset"}))


def test_anonymous_price_sort_requires_login():
    _assert_login_required(client.get("/api/v1/artworks", params={"sort": "price_asc"}))


def test_anonymous_price_filter_requires_login():
    _assert_login_required(client.get("/api/v1/artworks", params={"min_price": "1"}))


def test_anonymous_artist_paging_requires_login():
    _assert_login_required(client.get("/api/v1/artists", params={"cursor": "anything"}))


def test_garbage_bearer_token_is_401_not_a_silent_downgrade():
    for path in ("/api/v1/artworks", "/api/v1/artists"):
        response = client.get(path, headers={"Authorization": "Bearer not-a-real-token"})
        assert response.status_code == 401, path
        assert response.json() == {"detail": "Not authenticated"}, path
