"""Testimonials endpoint: public (no auth, no teaser), published rows only, in sort_order.

Uses an overridden DB dependency with a fake session so no Postgres is needed; the real
query is verified against the live deployment after merge.
"""
import os
import uuid
from types import SimpleNamespace

os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@localhost/db")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.dialects import postgresql  # noqa: E402

from app.db.session import get_db  # noqa: E402
from app.main import app  # noqa: E402


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return SimpleNamespace(all=lambda: self._rows)


class FakeSession:
    def __init__(self, rows):
        self.rows = rows
        self.statements = []

    async def execute(self, stmt):
        self.statements.append(stmt)
        return FakeResult(self.rows)


def _row(name, role=None, avatar=None, quote="Lovely."):
    return SimpleNamespace(id=uuid.uuid4(), author_name=name, author_role=role, avatar_url=avatar, quote=quote)


def _client(rows):
    session = FakeSession(rows)

    async def override():
        yield session

    app.dependency_overrides[get_db] = override
    return TestClient(app), session


def teardown_function():
    app.dependency_overrides.clear()


def test_anonymous_gets_200_with_items_and_cache_header():
    client, _ = _client([_row("A", "Collector", "https://x/a.png"), _row("B")])
    r = client.get("/api/v1/testimonials")
    assert r.status_code == 200
    body = r.json()
    assert [i["author_name"] for i in body["items"]] == ["A", "B"]
    assert set(body["items"][0]) == {"id", "author_name", "author_role", "avatar_url", "quote"}
    assert body["items"][1]["author_role"] is None and body["items"][1]["avatar_url"] is None
    assert r.headers["cache-control"] == "public, max-age=60"
    assert "teaser" not in body


def test_empty_is_200_with_empty_items():
    client, _ = _client([])
    r = client.get("/api/v1/testimonials")
    assert r.status_code == 200 and r.json() == {"items": []}


def test_query_filters_published_and_orders_by_sort_order():
    client, session = _client([])
    client.get("/api/v1/testimonials")
    sql = str(session.statements[0].compile(dialect=postgresql.dialect())).lower()
    assert "is_published is true" in sql
    assert "order by testimonials.sort_order, testimonials.id" in sql
