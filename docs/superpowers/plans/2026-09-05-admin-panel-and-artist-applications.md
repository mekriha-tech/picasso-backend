# Admin Panel + Artist Application Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the artist-application submission API, the admin approve/reject/moderate JSON API, and a bare-bones server-rendered HTML admin panel on top of it — so a user can apply to be an artist, an admin can approve or reject that application (auto-creating the artist's profile + 3 draft artworks per PRD rule 5), and an admin can moderate artwork status, all through a real login-gated panel.

**Architecture:** New tables (`artist_applications`, `application_works`, `artist_profiles`, `artworks`, `artwork_images`) feed a service layer (`app/services/`) that both a JSON API (`/api/v1/me/artist-application*`, `/api/v1/admin/*`) and a server-rendered HTML panel (`/admin/*`, session-cookie authenticated, entirely separate from the customer JWT/refresh-token system) call — one set of business rules, two front doors.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 async, Alembic, Pydantic v2, Jinja2 (new), Starlette `SessionMiddleware` (new, via `itsdangerous`), `python-multipart` (new, for HTML form posts), pytest (new).

**Spec:** `docs/superpowers/specs/2026-09-05-admin-panel-and-artist-applications-design.md`

## Global Constraints

- Money is `NUMERIC(12,2)`, never float (CLAUDE.md) — `artworks.price`/`width_cm`/`height_cm` use SQLAlchemy `Numeric`, not `Float`.
- Timestamps are `TIMESTAMPTZ`; API returns ISO 8601 UTC (CLAUDE.md) — every datetime column is `DateTime(timezone=True)`.
- The schema's CHECK constraints and partial unique indexes encode domain rules; do not simplify them away (CLAUDE.md) — every constraint from PRD §3.3–§3.5 must appear in both the model and the migration.
- Business rules live in the service layer, not in route handlers (CLAUDE.md) — routes only translate HTTP ↔ service calls; every task below keeps that boundary.
- The PRD §4.6 validation copy (`"Submit three works…"`, `"Tell us your primary medium."`) must be reused verbatim (CLAUDE.md) — see Task 8.
- No local Postgres is reachable from this dev environment. Every migration task verifies with `alembic upgrade head --sql` (confirmed working offline in this repo — generates the SQL without a live DB connection); every model/schema/service task verifies with `python -c "import ast; ast.parse(...)"` plus a fresh-venv `pip install -r requirements.txt`; pure-logic pieces get real pytest unit tests. DB-touching business logic (services, routes) is verified end-to-end against the live Railway deployment as the final task, matching the pattern already used for the auth work in this repo's history.
- This repo's existing convention for "no relationships": models only declare FK columns, never SQLAlchemy `relationship()` — every join is an explicit `select()` in the service layer. Keep matching it.

---

### Task 1: Add new dependencies

**Files:**
- Modify: `requirements.txt`

**Interfaces:**
- Produces: `itsdangerous` (Starlette `SessionMiddleware`'s signing backend), `Jinja2` (`fastapi.templating.Jinja2Templates`), `python-multipart` (required by FastAPI to parse `Form(...)` fields), `pytest` (test runner) — all importable in later tasks.

- [ ] **Step 1: Add the four new lines to `requirements.txt`, alphabetically**

Insert `itsdangerous==2.2.0` after `idna==3.19` and before `Mako==1.4.1`; insert `Jinja2==3.1.6` right after that (before `Mako`); insert `pytest==9.1.1` after `pydantic_core==2.46.5` and before `python-dotenv==1.2.3`; insert `python-multipart==0.0.32` after `python-jose==3.5.0` and before `PyYAML==6.0.3`. The full resulting file, in order:

```
alembic==1.19.1
annotated-doc==0.0.5
annotated-types==0.8.0
anyio==4.14.2
argon2-cffi==25.1.0
argon2-cffi-bindings==26.1.0
asyncpg==0.31.0
cffi==2.1.1
click==8.5.0
cryptography==50.0.1
ecdsa==0.19.2
fastapi==0.141.1
greenlet==3.5.5
h11==0.16.0
httptools==0.8.0
idna==3.19
itsdangerous==2.2.0
Jinja2==3.1.6
Mako==1.4.1
MarkupSafe==3.0.3
passlib==1.7.4
psycopg2-binary==2.9.12
pyasn1==0.6.4
pycparser==3.0
pydantic==2.13.5
pydantic-settings==2.15.0
pydantic_core==2.46.5
pytest==9.1.1
python-dotenv==1.2.3
python-jose==3.5.0
python-multipart==0.0.32
PyYAML==6.0.3
rsa==4.9.1
six==1.17.0
SQLAlchemy==2.0.52
starlette==1.6.0
typing-inspection==0.4.4
typing_extensions==4.16.0
uvicorn==0.52.4
watchfiles==1.2.0
websockets==17.1
```

- [ ] **Step 2: Verify a fresh venv installs cleanly**

Run:
```bash
python -m venv .venv_verify && ".venv_verify/Scripts/python.exe" -m pip install -r requirements.txt
```
Expected: no errors. Then delete the venv (`rm -rf .venv_verify`) — never commit it.

- [ ] **Step 3: Commit**

```bash
git add requirements.txt
git commit -m "chore: add itsdangerous, Jinja2, python-multipart, pytest for admin panel work"
```

---

### Task 2: Pure helper functions — slugs and reapply cooldown (TDD)

**Files:**
- Create: `pytest.ini`
- Create: `app/services/slugs.py`
- Create: `app/services/cooldowns.py`
- Test: `tests/test_slugs.py`
- Test: `tests/test_cooldowns.py`

**Interfaces:**
- Produces: `slugify(text: str) -> str`, `generate_unique_slug(base_text: str, existing_slugs: set[str]) -> str` (used by Task 9's `approve_application`); `REAPPLY_COOLDOWN_DAYS: int`, `reapply_available_at(rejected_at: datetime) -> datetime`, `is_in_reapply_cooldown(rejected_at: datetime, now: datetime) -> bool` (used by Task 8's `submit_application`).

- [ ] **Step 1: Create `pytest.ini` so `app` imports resolve regardless of invocation directory**

```ini
[pytest]
pythonpath = .
```

- [ ] **Step 2: Write the failing test for slugs**

```python
# tests/test_slugs.py
from app.services.slugs import slugify, generate_unique_slug


def test_slugify_lowercases_and_hyphenates():
    assert slugify("Elena D' Frost") == "elena-d-frost"


def test_slugify_strips_leading_trailing_punctuation():
    assert slugify("  --Abstract Painting!!--  ") == "abstract-painting"


def test_slugify_empty_input_falls_back():
    assert slugify("###") == "item"


def test_generate_unique_slug_no_collision():
    assert generate_unique_slug("Unique Name", set()) == "unique-name"


def test_generate_unique_slug_single_collision():
    assert generate_unique_slug("Elena Frost", {"elena-frost"}) == "elena-frost-2"


def test_generate_unique_slug_chained_collision():
    existing = {"elena-frost", "elena-frost-2", "elena-frost-3"}
    assert generate_unique_slug("Elena Frost", existing) == "elena-frost-4"
```

- [ ] **Step 3: Run it, confirm it fails**

Run: `python -m pytest tests/test_slugs.py -v`
Expected: `ModuleNotFoundError: No module named 'app.services.slugs'` (or similar collection error) — `app/services/slugs.py` doesn't exist yet.

- [ ] **Step 4: Implement `app/services/slugs.py`**

```python
import re


def slugify(text: str) -> str:
    """Lowercases, strips to alphanumerics-and-hyphens. Never returns an empty string."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "item"


def generate_unique_slug(base_text: str, existing_slugs: set[str]) -> str:
    """Slugifies base_text, appending -2, -3, ... until it doesn't collide with existing_slugs."""
    base = slugify(base_text)
    if base not in existing_slugs:
        return base
    suffix = 2
    while f"{base}-{suffix}" in existing_slugs:
        suffix += 1
    return f"{base}-{suffix}"
```

- [ ] **Step 5: Run it, confirm it passes**

Run: `python -m pytest tests/test_slugs.py -v`
Expected: 6 passed.

- [ ] **Step 6: Write the failing test for the reapply cooldown**

```python
# tests/test_cooldowns.py
from datetime import datetime, timedelta, timezone

from app.services.cooldowns import reapply_available_at, is_in_reapply_cooldown


def test_reapply_available_at_adds_30_days():
    rejected_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert reapply_available_at(rejected_at) == datetime(2026, 1, 31, tzinfo=timezone.utc)


def test_is_in_cooldown_true_the_day_after_rejection():
    rejected_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    now = rejected_at + timedelta(days=1)
    assert is_in_reapply_cooldown(rejected_at, now) is True


def test_is_in_cooldown_false_after_30_days():
    rejected_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    now = rejected_at + timedelta(days=31)
    assert is_in_reapply_cooldown(rejected_at, now) is False
```

- [ ] **Step 7: Run it, confirm it fails**

Run: `python -m pytest tests/test_cooldowns.py -v`
Expected: `ModuleNotFoundError: No module named 'app.services.cooldowns'`

- [ ] **Step 8: Implement `app/services/cooldowns.py`**

```python
from datetime import datetime, timedelta

REAPPLY_COOLDOWN_DAYS = 30


def reapply_available_at(rejected_at: datetime) -> datetime:
    """The PRD rule-6 date after which a rejected applicant may submit again."""
    return rejected_at + timedelta(days=REAPPLY_COOLDOWN_DAYS)


def is_in_reapply_cooldown(rejected_at: datetime, now: datetime) -> bool:
    return now < reapply_available_at(rejected_at)
```

- [ ] **Step 9: Run both test files, confirm everything passes**

Run: `python -m pytest tests/ -v`
Expected: 9 passed.

- [ ] **Step 10: Commit**

```bash
git add pytest.ini app/services/slugs.py app/services/cooldowns.py tests/test_slugs.py tests/test_cooldowns.py
git commit -m "feat: add slug-generation and reapply-cooldown pure helpers"
```

---

### Task 3: `artist_applications` + `application_works` models and migration

**Files:**
- Create: `app/models/artist_application.py`
- Create: `app/models/application_work.py`
- Modify: `app/models/__init__.py`
- Modify: `migrations/env.py`
- Create: `migrations/versions/e91a4c6f2b8d_create_artist_applications.py`

**Interfaces:**
- Produces: `ApplicationStatus(str, enum.Enum)` (`draft`/`submitted`/`under_review`/`approved`/`rejected`), `ArtistApplication` model (fields: `id`, `user_id`, `status`, `full_name`, `location`, `primary_medium`, `years_practising`, `website_url`, `instagram`, `statement`, `submitted_at`, `reviewed_at`, `reviewed_by`, `rejection_reason`, `created_at`, `updated_at`), `ApplicationWork` model (fields: `id`, `application_id`, `slot_index`, `title`, `year`, `medium`, `dimensions`, `image_url`) — both consumed by every later task.

- [ ] **Step 1: Create `app/models/artist_application.py`**

```python
import enum
import uuid
from datetime import datetime
from sqlalchemy import Text, SmallInteger, DateTime, ForeignKey, Enum as SQLEnum, func, text, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from app.db.session import Base


class ApplicationStatus(str, enum.Enum):
    draft = "draft"
    submitted = "submitted"
    under_review = "under_review"
    approved = "approved"
    rejected = "rejected"


class ArtistApplication(Base):
    __tablename__ = "artist_applications"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[ApplicationStatus] = mapped_column(
        SQLEnum(ApplicationStatus, name="application_status"),
        default=ApplicationStatus.draft,
        server_default="draft",
        nullable=False,
    )
    full_name: Mapped[str] = mapped_column(Text, nullable=False)
    location: Mapped[str] = mapped_column(Text, nullable=False)
    primary_medium: Mapped[str] = mapped_column(Text, nullable=False)
    years_practising: Mapped[int | None] = mapped_column(SmallInteger)
    website_url: Mapped[str | None] = mapped_column(Text)
    instagram: Mapped[str | None] = mapped_column(Text)
    statement: Mapped[str | None] = mapped_column(Text)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index(
            "one_open_application_per_user",
            "user_id",
            unique=True,
            postgresql_where=text("status IN ('draft','submitted','under_review')"),
        ),
    )
```

- [ ] **Step 2: Create `app/models/application_work.py`**

```python
import uuid
from sqlalchemy import Text, SmallInteger, ForeignKey, CheckConstraint, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from app.db.session import Base


class ApplicationWork(Base):
    __tablename__ = "application_works"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("artist_applications.id", ondelete="CASCADE"), nullable=False
    )
    slot_index: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    year: Mapped[int | None] = mapped_column(SmallInteger)
    medium: Mapped[str | None] = mapped_column(Text)
    dimensions: Mapped[str | None] = mapped_column(Text)
    image_url: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        CheckConstraint("slot_index BETWEEN 0 AND 2", name="application_works_slot_index_check"),
        UniqueConstraint("application_id", "slot_index", name="uq_application_works_slot"),
    )
```

- [ ] **Step 3: Register both models in `app/models/__init__.py`**

```python
from .user import User
from app.models.refresh_token import RefreshToken
from app.models.artist_application import ArtistApplication
from app.models.application_work import ApplicationWork
```

- [ ] **Step 4: Register both models in `migrations/env.py`** (so `Base.metadata` stays complete for any future autogenerate diff, matching the existing pattern)

Add after the existing `from app.models.refresh_token import RefreshToken` line:
```python
from app.models.artist_application import ArtistApplication
from app.models.application_work import ApplicationWork
```

- [ ] **Step 5: Create the migration**

```python
# migrations/versions/e91a4c6f2b8d_create_artist_applications.py
"""create_artist_applications_and_application_works

Revision ID: e91a4c6f2b8d
Revises: a5c77090a161
Create Date: 2026-09-05 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'e91a4c6f2b8d'
down_revision: Union[str, Sequence[str], None] = 'a5c77090a161'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    application_status = postgresql.ENUM(
        'draft', 'submitted', 'under_review', 'approved', 'rejected',
        name='application_status',
    )
    application_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        'artist_applications',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('status', application_status, server_default='draft', nullable=False),
        sa.Column('full_name', sa.Text(), nullable=False),
        sa.Column('location', sa.Text(), nullable=False),
        sa.Column('primary_medium', sa.Text(), nullable=False),
        sa.Column('years_practising', sa.SmallInteger(), nullable=True),
        sa.Column('website_url', sa.Text(), nullable=True),
        sa.Column('instagram', sa.Text(), nullable=True),
        sa.Column('statement', sa.Text(), nullable=True),
        sa.Column('submitted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('reviewed_by', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('rejection_reason', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['reviewed_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'one_open_application_per_user', 'artist_applications', ['user_id'], unique=True,
        postgresql_where=sa.text("status IN ('draft','submitted','under_review')"),
    )

    op.create_table(
        'application_works',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('application_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('slot_index', sa.SmallInteger(), nullable=False),
        sa.Column('title', sa.Text(), nullable=False),
        sa.Column('year', sa.SmallInteger(), nullable=True),
        sa.Column('medium', sa.Text(), nullable=True),
        sa.Column('dimensions', sa.Text(), nullable=True),
        sa.Column('image_url', sa.Text(), nullable=False),
        sa.CheckConstraint('slot_index BETWEEN 0 AND 2', name='application_works_slot_index_check'),
        sa.ForeignKeyConstraint(['application_id'], ['artist_applications.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('application_id', 'slot_index', name='uq_application_works_slot'),
    )


def downgrade() -> None:
    op.drop_table('application_works')
    op.drop_index('one_open_application_per_user', table_name='artist_applications')
    op.drop_table('artist_applications')
    application_status = postgresql.ENUM(name='application_status')
    application_status.drop(op.get_bind(), checkfirst=True)
```

- [ ] **Step 6: Verify with `ast.parse`**

Run: `python -c "import ast; [ast.parse(open(f).read()) for f in ['app/models/artist_application.py','app/models/application_work.py','app/models/__init__.py','migrations/env.py','migrations/versions/e91a4c6f2b8d_create_artist_applications.py']]; print('OK')"`
Expected: `OK`

- [ ] **Step 7: Verify the migration generates valid SQL without a live DB**

Run (fill in any placeholder Postgres env vars — the URL is never actually connected to in `--sql` mode):
```bash
POSTGRES_SERVER=localhost POSTGRES_USER=x POSTGRES_PASSWORD=x POSTGRES_DB=x SECRET_KEY=test python -m alembic upgrade head --sql
```
Expected: the output ends with the new `CREATE TYPE application_status`, `CREATE TABLE artist_applications`, `CREATE UNIQUE INDEX one_open_application_per_user`, and `CREATE TABLE application_works` statements, with no errors.

- [ ] **Step 8: Commit**

```bash
git add app/models/artist_application.py app/models/application_work.py app/models/__init__.py migrations/env.py migrations/versions/e91a4c6f2b8d_create_artist_applications.py
git commit -m "feat: add artist_applications and application_works tables"
```

---

### Task 4: `artist_profiles` model and migration

**Files:**
- Create: `app/models/artist_profile.py`
- Modify: `app/models/__init__.py`
- Modify: `migrations/env.py`
- Create: `migrations/versions/f2b8d5a91c34_create_artist_profiles.py`

**Interfaces:**
- Consumes: nothing new (independent table, FK to `users`)
- Produces: `ArtistProfile` model (fields: `id`, `user_id`, `display_name`, `slug`, `primary_medium`, `years_practising`, `statement`, `website_url`, `instagram`, `cover_image_url`, `is_featured`, `approved_at`, `created_at`, `updated_at`) — consumed by Task 9's `approve_application` and Task 10's artwork FK.

- [ ] **Step 1: Create `app/models/artist_profile.py`**

```python
import uuid
from datetime import datetime
from sqlalchemy import Text, SmallInteger, Boolean, DateTime, ForeignKey, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from app.db.session import Base


class ArtistProfile(Base):
    __tablename__ = "artist_profiles"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    slug: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    primary_medium: Mapped[str] = mapped_column(Text, nullable=False)
    years_practising: Mapped[int | None] = mapped_column(SmallInteger)
    statement: Mapped[str | None] = mapped_column(Text)
    website_url: Mapped[str | None] = mapped_column(Text)
    instagram: Mapped[str | None] = mapped_column(Text)
    cover_image_url: Mapped[str | None] = mapped_column(Text)
    is_featured: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
```

- [ ] **Step 2: Register in `app/models/__init__.py`** (append `from app.models.artist_profile import ArtistProfile`) and in `migrations/env.py` (append `from app.models.artist_profile import ArtistProfile`)

- [ ] **Step 3: Create the migration**

```python
# migrations/versions/f2b8d5a91c34_create_artist_profiles.py
"""create_artist_profiles_table

Revision ID: f2b8d5a91c34
Revises: e91a4c6f2b8d
Create Date: 2026-09-05 12:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'f2b8d5a91c34'
down_revision: Union[str, Sequence[str], None] = 'e91a4c6f2b8d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'artist_profiles',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('display_name', sa.Text(), nullable=False),
        sa.Column('slug', sa.Text(), nullable=False),
        sa.Column('primary_medium', sa.Text(), nullable=False),
        sa.Column('years_practising', sa.SmallInteger(), nullable=True),
        sa.Column('statement', sa.Text(), nullable=True),
        sa.Column('website_url', sa.Text(), nullable=True),
        sa.Column('instagram', sa.Text(), nullable=True),
        sa.Column('cover_image_url', sa.Text(), nullable=True),
        sa.Column('is_featured', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('approved_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id'),
        sa.UniqueConstraint('slug'),
    )


def downgrade() -> None:
    op.drop_table('artist_profiles')
```

- [ ] **Step 4: Verify with `ast.parse`** (same pattern as Task 3 Step 6, for the three changed/new files)

- [ ] **Step 5: Verify with `alembic upgrade head --sql`** (same command as Task 3 Step 7)

Expected: output now also ends with `CREATE TABLE artist_profiles`.

- [ ] **Step 6: Commit**

```bash
git add app/models/artist_profile.py app/models/__init__.py migrations/env.py migrations/versions/f2b8d5a91c34_create_artist_profiles.py
git commit -m "feat: add artist_profiles table"
```

---

### Task 5: `artworks` + `artwork_images` models and migration

**Files:**
- Create: `app/models/artwork.py`
- Create: `app/models/artwork_image.py`
- Modify: `app/models/__init__.py`
- Modify: `migrations/env.py`
- Create: `migrations/versions/a4c6f2b8d5e9_create_artworks.py`

**Interfaces:**
- Consumes: `ArtistProfile` (Task 4, FK target)
- Produces: `ListingType(str, enum.Enum)` (`sale`/`auction`/`display`), `ArtworkStatus(str, enum.Enum)` (`draft`/`published`/`reserved`/`sold`/`unlisted`/`removed`), `Artwork` model, `ArtworkImage` model — consumed by Task 9 (`approve_application` creates these) and Task 10 (`artworks.py` service, admin JSON API).

- [ ] **Step 1: Create `app/models/artwork.py`**

```python
import enum
import uuid
from datetime import datetime
from sqlalchemy import (
    Text, SmallInteger, Integer, Numeric, Boolean, DateTime, ForeignKey,
    Enum as SQLEnum, CheckConstraint, func, text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from app.db.session import Base


class ListingType(str, enum.Enum):
    sale = "sale"
    auction = "auction"
    display = "display"


class ArtworkStatus(str, enum.Enum):
    draft = "draft"
    published = "published"
    reserved = "reserved"
    sold = "sold"
    unlisted = "unlisted"
    removed = "removed"


class Artwork(Base):
    __tablename__ = "artworks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    artist_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("artist_profiles.id", ondelete="RESTRICT"), nullable=False
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    slug: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    year: Mapped[int | None] = mapped_column(SmallInteger)
    medium: Mapped[str | None] = mapped_column(Text)
    dimensions: Mapped[str | None] = mapped_column(Text)
    width_cm: Mapped[float | None] = mapped_column(Numeric(8, 2))
    height_cm: Mapped[float | None] = mapped_column(Numeric(8, 2))
    category: Mapped[str | None] = mapped_column(Text)
    listing_type: Mapped[ListingType] = mapped_column(
        SQLEnum(ListingType, name="listing_type"),
        default=ListingType.display,
        server_default="display",
        nullable=False,
    )
    status: Mapped[ArtworkStatus] = mapped_column(
        SQLEnum(ArtworkStatus, name="artwork_status"),
        default=ArtworkStatus.draft,
        server_default="draft",
        nullable=False,
    )
    price: Mapped[float | None] = mapped_column(Numeric(12, 2))
    sold: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    sold_price: Mapped[float | None] = mapped_column(Numeric(12, 2))
    sold_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    primary_image_url: Mapped[str | None] = mapped_column(Text)
    view_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("listing_type <> 'sale' OR price IS NOT NULL", name="sale_needs_price"),
        CheckConstraint("listing_type <> 'display' OR price IS NULL", name="display_has_no_price"),
    )
```

- [ ] **Step 2: Create `app/models/artwork_image.py`**

```python
import uuid
from sqlalchemy import Text, SmallInteger, Integer, Boolean, ForeignKey, Index, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from app.db.session import Base


class ArtworkImage(Base):
    __tablename__ = "artwork_images"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    artwork_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("artworks.id", ondelete="CASCADE"), nullable=False
    )
    url: Mapped[str] = mapped_column(Text, nullable=False)
    alt_text: Mapped[str | None] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0")
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    width_px: Mapped[int | None] = mapped_column(Integer)
    height_px: Mapped[int | None] = mapped_column(Integer)

    __table_args__ = (
        Index("one_primary_image_per_artwork", "artwork_id", unique=True, postgresql_where=text("is_primary")),
    )
```

- [ ] **Step 3: Register both models in `app/models/__init__.py`** (append `from app.models.artwork import Artwork` and `from app.models.artwork_image import ArtworkImage`) and in `migrations/env.py` (same two imports)

- [ ] **Step 4: Create the migration**

```python
# migrations/versions/a4c6f2b8d5e9_create_artworks.py
"""create_artworks_and_artwork_images_tables

Revision ID: a4c6f2b8d5e9
Revises: f2b8d5a91c34
Create Date: 2026-09-05 12:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'a4c6f2b8d5e9'
down_revision: Union[str, Sequence[str], None] = 'f2b8d5a91c34'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    listing_type = postgresql.ENUM('sale', 'auction', 'display', name='listing_type')
    listing_type.create(op.get_bind(), checkfirst=True)
    artwork_status = postgresql.ENUM(
        'draft', 'published', 'reserved', 'sold', 'unlisted', 'removed', name='artwork_status',
    )
    artwork_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        'artworks',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('artist_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('title', sa.Text(), nullable=False),
        sa.Column('slug', sa.Text(), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('year', sa.SmallInteger(), nullable=True),
        sa.Column('medium', sa.Text(), nullable=True),
        sa.Column('dimensions', sa.Text(), nullable=True),
        sa.Column('width_cm', sa.Numeric(8, 2), nullable=True),
        sa.Column('height_cm', sa.Numeric(8, 2), nullable=True),
        sa.Column('category', sa.Text(), nullable=True),
        sa.Column('listing_type', listing_type, server_default='display', nullable=False),
        sa.Column('status', artwork_status, server_default='draft', nullable=False),
        sa.Column('price', sa.Numeric(12, 2), nullable=True),
        sa.Column('sold', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('sold_price', sa.Numeric(12, 2), nullable=True),
        sa.Column('sold_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('primary_image_url', sa.Text(), nullable=True),
        sa.Column('view_count', sa.Integer(), server_default='0', nullable=False),
        sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("listing_type <> 'sale' OR price IS NOT NULL", name='sale_needs_price'),
        sa.CheckConstraint("listing_type <> 'display' OR price IS NULL", name='display_has_no_price'),
        sa.ForeignKeyConstraint(['artist_id'], ['artist_profiles.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('slug'),
    )

    op.create_table(
        'artwork_images',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('artwork_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('url', sa.Text(), nullable=False),
        sa.Column('alt_text', sa.Text(), nullable=True),
        sa.Column('sort_order', sa.SmallInteger(), server_default='0', nullable=False),
        sa.Column('is_primary', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('width_px', sa.Integer(), nullable=True),
        sa.Column('height_px', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['artwork_id'], ['artworks.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'one_primary_image_per_artwork', 'artwork_images', ['artwork_id'], unique=True,
        postgresql_where=sa.text('is_primary'),
    )


def downgrade() -> None:
    op.drop_index('one_primary_image_per_artwork', table_name='artwork_images')
    op.drop_table('artwork_images')
    op.drop_table('artworks')
    artwork_status = postgresql.ENUM(name='artwork_status')
    artwork_status.drop(op.get_bind(), checkfirst=True)
    listing_type = postgresql.ENUM(name='listing_type')
    listing_type.drop(op.get_bind(), checkfirst=True)
```

- [ ] **Step 5: Verify with `ast.parse`** (all new/changed files)

- [ ] **Step 6: Verify with `alembic upgrade head --sql`**

Expected: output now also ends with `CREATE TYPE listing_type`, `CREATE TYPE artwork_status`, `CREATE TABLE artworks`, `CREATE TABLE artwork_images`, `CREATE UNIQUE INDEX one_primary_image_per_artwork`.

- [ ] **Step 7: Commit**

```bash
git add app/models/artwork.py app/models/artwork_image.py app/models/__init__.py migrations/env.py migrations/versions/a4c6f2b8d5e9_create_artworks.py
git commit -m "feat: add artworks and artwork_images tables"
```

---

### Task 6: Pydantic schemas for artist applications and artworks

**Files:**
- Create: `app/schemas/artist_application.py`
- Create: `app/schemas/artwork.py`

**Interfaces:**
- Consumes: nothing (pure Pydantic, no DB)
- Produces: `ApplicationWorkIn`, `ApplicationWorkOut`, `ArtistApplicationIn`, `ArtistApplicationOut`, `ArtistApplicationAdminOut`, `RejectRequest` (Task 7/8/9/10 routes); `ArtworkOut`, `ArtworkStatusUpdate` (Task 10 routes)

- [ ] **Step 1: Create `app/schemas/artist_application.py`**

```python
import uuid
from datetime import datetime
from pydantic import BaseModel


class ApplicationWorkIn(BaseModel):
    title: str
    image_url: str
    year: int | None = None
    medium: str | None = None
    dimensions: str | None = None


class ApplicationWorkOut(BaseModel):
    slot_index: int
    title: str
    year: int | None
    medium: str | None
    dimensions: str | None
    image_url: str


class ArtistApplicationIn(BaseModel):
    full_name: str
    location: str
    primary_medium: str
    years_practising: int | None = None
    website_url: str | None = None
    instagram: str | None = None
    statement: str | None = None


class ArtistApplicationOut(BaseModel):
    id: uuid.UUID
    status: str
    full_name: str
    location: str
    primary_medium: str
    years_practising: int | None
    website_url: str | None
    instagram: str | None
    statement: str | None
    submitted_at: datetime | None
    reviewed_at: datetime | None
    rejection_reason: str | None
    works: list[ApplicationWorkOut]


class ArtistApplicationAdminOut(ArtistApplicationOut):
    user_id: uuid.UUID
    applicant_email: str


class RejectRequest(BaseModel):
    reason: str
```

- [ ] **Step 2: Create `app/schemas/artwork.py`**

```python
import uuid
from datetime import datetime
from pydantic import BaseModel


class ArtworkOut(BaseModel):
    id: uuid.UUID
    artist_id: uuid.UUID
    title: str
    slug: str
    listing_type: str
    status: str
    primary_image_url: str | None
    created_at: datetime

    class Config:
        from_attributes = True


class ArtworkStatusUpdate(BaseModel):
    status: str
```

Note: `ArtworkOut` uses `from_attributes = True` because its fields map 1:1 onto the `Artwork` ORM model (same pattern as `UserResponse` in `app/schemas/user.py`) — routes can return the ORM object directly. `ArtistApplicationOut` does **not**, because `works` isn't an ORM relationship attribute (this codebase doesn't use `relationship()` anywhere) — routes build that dict by hand (see Task 7).

- [ ] **Step 3: Verify with `ast.parse`**

Run: `python -c "import ast; [ast.parse(open(f).read()) for f in ['app/schemas/artist_application.py','app/schemas/artwork.py']]; print('OK')"`

- [ ] **Step 4: Commit**

```bash
git add app/schemas/artist_application.py app/schemas/artwork.py
git commit -m "feat: add Pydantic schemas for artist applications and artworks"
```

---

### Task 7: Draft application + work-slot service and API

**Files:**
- Create: `app/services/artist_applications.py`
- Create: `app/api/v1/artist_applications.py`
- Modify: `app/main.py`

**Interfaces:**
- Consumes: `ArtistApplication`, `ApplicationStatus` (Task 3), `ApplicationWork` (Task 3), `ArtistApplicationIn`/`ArtistApplicationOut`/`ApplicationWorkIn`/`ApplicationWorkOut` (Task 6), `get_current_user` (`app/api/deps.py`, already exists)
- Produces: `ApplicationNotEditableError` (exception, also used by Tasks 8 & 9), `get_open_application(db, user_id) -> ArtistApplication | None`, `get_application_works(db, application_id) -> list[ApplicationWork]`, `get_application_by_id(db, application_id) -> ArtistApplication | None`, `create_or_update_draft(db, user_id, data: ArtistApplicationIn) -> ArtistApplication`, `set_application_work(db, application, slot_index, data: ApplicationWorkIn) -> ApplicationWork`, `clear_application_work(db, application, slot_index) -> None` — all consumed by Task 8 (submit), Task 9 (approve/reject), Task 10 (admin API), Task 12 (HTML panel).

**Design note:** `GET /me/artist-application` does **not** auto-create an empty draft (a deviation from the merged spec's parenthetical, caught during planning) — `full_name`/`location`/`primary_medium` are `NOT NULL` per PRD §3.4, so there's no such thing as an empty row. `POST /me/artist-application` is what creates the first row, and it requires those three fields from the start (matching PRD rule 2: "primary_medium is required. Name and location are required."). `GET` 404s until the first `POST`.

- [ ] **Step 1: Create `app/services/artist_applications.py`**

```python
import uuid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.artist_application import ArtistApplication, ApplicationStatus
from app.models.application_work import ApplicationWork
from app.schemas.artist_application import ArtistApplicationIn, ApplicationWorkIn


class ApplicationNotEditableError(Exception):
    """Raised when trying to change an application that's no longer a draft, or no longer
    submitted/under_review (see Task 9's use of this for approve/reject/claim)."""


async def get_open_application(db: AsyncSession, user_id: uuid.UUID) -> ArtistApplication | None:
    result = await db.execute(
        select(ArtistApplication)
        .where(ArtistApplication.user_id == user_id)
        .where(ArtistApplication.status.in_([
            ApplicationStatus.draft, ApplicationStatus.submitted, ApplicationStatus.under_review,
        ]))
    )
    return result.scalars().first()


async def get_application_by_id(db: AsyncSession, application_id: uuid.UUID) -> ArtistApplication | None:
    result = await db.execute(select(ArtistApplication).where(ArtistApplication.id == application_id))
    return result.scalars().first()


async def get_application_works(db: AsyncSession, application_id: uuid.UUID) -> list[ApplicationWork]:
    result = await db.execute(
        select(ApplicationWork)
        .where(ApplicationWork.application_id == application_id)
        .order_by(ApplicationWork.slot_index)
    )
    return list(result.scalars().all())


async def create_or_update_draft(
    db: AsyncSession, user_id: uuid.UUID, data: ArtistApplicationIn
) -> ArtistApplication:
    application = await get_open_application(db, user_id)
    if application is not None and application.status != ApplicationStatus.draft:
        raise ApplicationNotEditableError("Cannot edit an application after it's been submitted.")

    if application is None:
        application = ArtistApplication(user_id=user_id)
        db.add(application)

    application.full_name = data.full_name
    application.location = data.location
    application.primary_medium = data.primary_medium
    application.years_practising = data.years_practising
    application.website_url = data.website_url
    application.instagram = data.instagram
    application.statement = data.statement

    await db.commit()
    await db.refresh(application)
    return application


async def set_application_work(
    db: AsyncSession, application: ArtistApplication, slot_index: int, data: ApplicationWorkIn
) -> ApplicationWork:
    if application.status != ApplicationStatus.draft:
        raise ApplicationNotEditableError("Cannot edit an application after it's been submitted.")

    result = await db.execute(
        select(ApplicationWork)
        .where(ApplicationWork.application_id == application.id)
        .where(ApplicationWork.slot_index == slot_index)
    )
    work = result.scalars().first()
    if work is None:
        work = ApplicationWork(application_id=application.id, slot_index=slot_index)
        db.add(work)

    work.title = data.title
    work.image_url = data.image_url
    work.year = data.year
    work.medium = data.medium
    work.dimensions = data.dimensions

    await db.commit()
    await db.refresh(work)
    return work


async def clear_application_work(db: AsyncSession, application: ArtistApplication, slot_index: int) -> None:
    if application.status != ApplicationStatus.draft:
        raise ApplicationNotEditableError("Cannot edit an application after it's been submitted.")

    result = await db.execute(
        select(ApplicationWork)
        .where(ApplicationWork.application_id == application.id)
        .where(ApplicationWork.slot_index == slot_index)
    )
    work = result.scalars().first()
    if work is not None:
        await db.delete(work)
        await db.commit()
```

- [ ] **Step 2: Create `app/api/v1/artist_applications.py`**

```python
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.artist_application import (
    ArtistApplicationIn, ArtistApplicationOut, ApplicationWorkIn, ApplicationWorkOut,
)
from app.services import artist_applications as applications_service
from app.services.artist_applications import ApplicationNotEditableError

router = APIRouter()


def _to_out(application, works) -> dict:
    return {
        "id": application.id,
        "status": application.status,
        "full_name": application.full_name,
        "location": application.location,
        "primary_medium": application.primary_medium,
        "years_practising": application.years_practising,
        "website_url": application.website_url,
        "instagram": application.instagram,
        "statement": application.statement,
        "submitted_at": application.submitted_at,
        "reviewed_at": application.reviewed_at,
        "rejection_reason": application.rejection_reason,
        "works": [
            {
                "slot_index": w.slot_index,
                "title": w.title,
                "year": w.year,
                "medium": w.medium,
                "dimensions": w.dimensions,
                "image_url": w.image_url,
            }
            for w in works
        ],
    }


async def _get_own_open_application_or_404(db, current_user):
    application = await applications_service.get_open_application(db, current_user.id)
    if application is None:
        raise HTTPException(status_code=404, detail="No application yet")
    return application


@router.get("/me/artist-application", response_model=ArtistApplicationOut)
async def get_my_application(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    application = await _get_own_open_application_or_404(db, current_user)
    works = await applications_service.get_application_works(db, application.id)
    return _to_out(application, works)


@router.post("/me/artist-application", response_model=ArtistApplicationOut)
async def upsert_my_application(
    payload: ArtistApplicationIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        application = await applications_service.create_or_update_draft(db, current_user.id, payload)
    except ApplicationNotEditableError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    works = await applications_service.get_application_works(db, application.id)
    return _to_out(application, works)


@router.put("/me/artist-application/works/{slot}", response_model=ApplicationWorkOut)
async def set_my_application_work(
    slot: int,
    payload: ApplicationWorkIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if slot not in (0, 1, 2):
        raise HTTPException(status_code=422, detail="slot must be 0, 1, or 2")
    application = await _get_own_open_application_or_404(db, current_user)
    try:
        work = await applications_service.set_application_work(db, application, slot, payload)
    except ApplicationNotEditableError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {
        "slot_index": work.slot_index,
        "title": work.title,
        "year": work.year,
        "medium": work.medium,
        "dimensions": work.dimensions,
        "image_url": work.image_url,
    }


@router.delete("/me/artist-application/works/{slot}", status_code=204)
async def clear_my_application_work(
    slot: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if slot not in (0, 1, 2):
        raise HTTPException(status_code=422, detail="slot must be 0, 1, or 2")
    application = await _get_own_open_application_or_404(db, current_user)
    try:
        await applications_service.clear_application_work(db, application, slot)
    except ApplicationNotEditableError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
```

- [ ] **Step 3: Wire the router into `app/main.py`**

Add the import next to the existing `from app.api.v1 import auth` line:
```python
from app.api.v1 import auth, artist_applications
```
Add the include next to the existing `app.include_router(auth.router, ...)` line:
```python
app.include_router(
    artist_applications.router, prefix=settings.API_V1_PREFIX, tags=["Artist Application"]
)
```

- [ ] **Step 4: Verify with `ast.parse`** (`app/services/artist_applications.py`, `app/api/v1/artist_applications.py`, `app/main.py`)

- [ ] **Step 5: Commit**

```bash
git add app/services/artist_applications.py app/api/v1/artist_applications.py app/main.py
git commit -m "feat: add draft artist-application creation and work-slot endpoints"
```

---

### Task 8: Submit endpoint (validation + PRD §4.6 copy + reapply cooldown)

**Files:**
- Modify: `app/services/artist_applications.py`
- Modify: `app/api/v1/artist_applications.py`

**Interfaces:**
- Consumes: `get_application_works`, `ApplicationNotEditableError` (Task 7); `is_in_reapply_cooldown`, `reapply_available_at` (Task 2); `User`, `ArtistStatus` (`app/models/user.py`, already exists)
- Produces: `ApplicationValidationError` (exception carrying `.errors: dict[str, list[str]]`), `get_last_rejected_application(db, user_id) -> ArtistApplication | None`, `submit_application(db, application) -> ArtistApplication`

**Design note on error shape:** PRD §4.6 shows the validation body as `{"works": ["Submit three works…"]}` with no wrapper. The rest of this codebase's error responses are all plain FastAPI `HTTPException(detail=...)`, which always wraps as `{"detail": ...}` — already flagged as a gap against PRD's full RFC 7807 vision in `docs/API_REFERENCE.md`, and explicitly deferred there rather than fixed ad hoc per-endpoint. This task follows that existing convention rather than introduce a one-off exception just for this route: the response is `{"detail": {"works": ["Submit three works…"]}}`, preserving the PRD's exact field name and copy inside the wrapper this codebase already uses everywhere else. Only the `"works"` and `"primary_medium"` messages are the PRD's literal quoted copy; `"full_name"`/`"location"` aren't quoted anywhere in the PRD, so the messages below are a reasonable best guess in the same voice, not verbatim from a prototype — flag this to whoever owns final copy review.

- [ ] **Step 1: Add to `app/services/artist_applications.py`** (append; also add the needed imports at the top)

Add these imports to the top of the file (alongside the existing ones):
```python
from datetime import datetime, timezone
from sqlalchemy.sql import func
from app.models.user import User, ArtistStatus
from app.services.cooldowns import is_in_reapply_cooldown, reapply_available_at
```

Append to the bottom of the file:
```python
class ApplicationValidationError(Exception):
    def __init__(self, errors: dict[str, list[str]]):
        self.errors = errors
        super().__init__(str(errors))


async def get_last_rejected_application(db: AsyncSession, user_id: uuid.UUID) -> ArtistApplication | None:
    result = await db.execute(
        select(ArtistApplication)
        .where(ArtistApplication.user_id == user_id)
        .where(ArtistApplication.status == ApplicationStatus.rejected)
        .order_by(ArtistApplication.reviewed_at.desc())
    )
    return result.scalars().first()


async def submit_application(db: AsyncSession, application: ArtistApplication) -> ArtistApplication:
    if application.status != ApplicationStatus.draft:
        raise ApplicationNotEditableError("This application has already been submitted.")

    works = await get_application_works(db, application.id)
    errors: dict[str, list[str]] = {}
    if len(works) != 3:
        errors["works"] = ["Submit three works…"]
    if not application.primary_medium:
        errors["primary_medium"] = ["Tell us your primary medium."]
    if not application.full_name:
        errors["full_name"] = ["Tell us your name."]
    if not application.location:
        errors["location"] = ["Tell us your location."]
    if errors:
        raise ApplicationValidationError(errors)

    last_rejected = await get_last_rejected_application(db, application.user_id)
    if last_rejected is not None and last_rejected.reviewed_at is not None:
        now = datetime.now(timezone.utc)
        if is_in_reapply_cooldown(last_rejected.reviewed_at, now):
            available_at = reapply_available_at(last_rejected.reviewed_at)
            raise ApplicationValidationError(
                {"detail": [f"You can reapply on {available_at.date().isoformat()}."]}
            )

    application.status = ApplicationStatus.submitted
    application.submitted_at = func.now()

    result = await db.execute(select(User).where(User.id == application.user_id))
    user = result.scalars().first()
    user.artist_status = ArtistStatus.pending

    await db.commit()
    await db.refresh(application)
    return application
```

- [ ] **Step 2: Add the route to `app/api/v1/artist_applications.py`** (append to the end of the file)

```python
@router.post("/me/artist-application/submit", response_model=ArtistApplicationOut)
async def submit_my_application(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    application = await _get_own_open_application_or_404(db, current_user)
    try:
        application = await applications_service.submit_application(db, application)
    except ApplicationNotEditableError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except applications_service.ApplicationValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors)
    works = await applications_service.get_application_works(db, application.id)
    return _to_out(application, works)
```

- [ ] **Step 3: Verify with `ast.parse`** on both changed files

- [ ] **Step 4: Commit**

```bash
git add app/services/artist_applications.py app/api/v1/artist_applications.py
git commit -m "feat: add artist-application submit endpoint with PRD validation copy"
```

---

### Task 9: Approve / reject / claim service (PRD rule 5's transaction)

**Files:**
- Modify: `app/services/artist_applications.py`

**Interfaces:**
- Consumes: `ArtistProfile` (Task 4); `Artwork`, `ArtworkImage`, `ListingType`, `ArtworkStatus` (Task 5); `generate_unique_slug` (Task 2); `ApplicationNotEditableError`, `get_application_works` (Task 7)
- Produces: `claim_application(db, application, admin) -> ArtistApplication`, `approve_application(db, application, admin) -> ArtistProfile`, `reject_application(db, application, admin, reason) -> ArtistApplication`, `list_applications(db, status=None) -> list[tuple[ArtistApplication, list[ApplicationWork], str]]` — all consumed by Task 10's admin API and Task 12's HTML panel.

- [ ] **Step 1: Add imports to the top of `app/services/artist_applications.py`**

```python
from app.models.artist_profile import ArtistProfile
from app.models.artwork import Artwork, ListingType, ArtworkStatus
from app.models.artwork_image import ArtworkImage
from app.services.slugs import generate_unique_slug
```

- [ ] **Step 2: Append the claim/approve/reject/list functions**

```python
async def claim_application(db: AsyncSession, application: ArtistApplication, admin: User) -> ArtistApplication:
    if application.status != ApplicationStatus.submitted:
        raise ApplicationNotEditableError("Only a submitted application can be claimed.")
    application.status = ApplicationStatus.under_review
    application.reviewed_by = admin.id
    await db.commit()
    await db.refresh(application)
    return application


async def approve_application(db: AsyncSession, application: ArtistApplication, admin: User) -> ArtistProfile:
    if application.status not in (ApplicationStatus.submitted, ApplicationStatus.under_review):
        raise ApplicationNotEditableError("Only a submitted or under-review application can be approved.")

    works = await get_application_works(db, application.id)
    if len(works) != 3:
        raise ApplicationNotEditableError("Application no longer has exactly three works.")

    profile_slugs_result = await db.execute(select(ArtistProfile.slug))
    existing_profile_slugs = {row[0] for row in profile_slugs_result.all()}
    profile_slug = generate_unique_slug(application.full_name, existing_profile_slugs)

    profile = ArtistProfile(
        user_id=application.user_id,
        display_name=application.full_name,
        slug=profile_slug,
        primary_medium=application.primary_medium,
        years_practising=application.years_practising,
        statement=application.statement,
        website_url=application.website_url,
        instagram=application.instagram,
    )
    db.add(profile)
    await db.flush()

    artwork_slugs_result = await db.execute(select(Artwork.slug))
    existing_artwork_slugs = {row[0] for row in artwork_slugs_result.all()}

    for work in works:
        artwork_slug = generate_unique_slug(work.title, existing_artwork_slugs)
        existing_artwork_slugs.add(artwork_slug)
        artwork = Artwork(
            artist_id=profile.id,
            title=work.title,
            slug=artwork_slug,
            year=work.year,
            medium=work.medium,
            dimensions=work.dimensions,
            listing_type=ListingType.display,
            status=ArtworkStatus.draft,
            primary_image_url=work.image_url,
        )
        db.add(artwork)
        await db.flush()
        db.add(ArtworkImage(artwork_id=artwork.id, url=work.image_url, is_primary=True, sort_order=0))

    application.status = ApplicationStatus.approved
    application.reviewed_at = func.now()
    application.reviewed_by = admin.id

    result = await db.execute(select(User).where(User.id == application.user_id))
    user = result.scalars().first()
    user.artist_status = ArtistStatus.approved

    await db.commit()
    await db.refresh(profile)
    return profile


async def reject_application(
    db: AsyncSession, application: ArtistApplication, admin: User, reason: str
) -> ArtistApplication:
    if application.status not in (ApplicationStatus.submitted, ApplicationStatus.under_review):
        raise ApplicationNotEditableError("Only a submitted or under-review application can be rejected.")

    application.status = ApplicationStatus.rejected
    application.reviewed_at = func.now()
    application.reviewed_by = admin.id
    application.rejection_reason = reason

    result = await db.execute(select(User).where(User.id == application.user_id))
    user = result.scalars().first()
    user.artist_status = ArtistStatus.rejected

    await db.commit()
    await db.refresh(application)
    return application


async def list_applications(
    db: AsyncSession, status: ApplicationStatus | None = None
) -> list[tuple[ArtistApplication, list, str]]:
    query = select(ArtistApplication, User.email).join(User, User.id == ArtistApplication.user_id)
    if status is not None:
        query = query.where(ArtistApplication.status == status)
    query = query.order_by(ArtistApplication.created_at.desc())
    result = await db.execute(query)
    rows = []
    for application, email in result.all():
        works = await get_application_works(db, application.id)
        rows.append((application, works, email))
    return rows
```

- [ ] **Step 3: Verify with `ast.parse`**

- [ ] **Step 4: Commit**

```bash
git add app/services/artist_applications.py
git commit -m "feat: add approve/reject/claim/list-applications service functions"
```

---

### Task 10: Artworks service + admin JSON API

**Files:**
- Create: `app/services/artworks.py`
- Create: `app/api/v1/admin.py`
- Modify: `app/main.py`

**Interfaces:**
- Consumes: `Artwork`, `ArtworkStatus` (Task 5); `claim_application`/`approve_application`/`reject_application`/`list_applications`/`get_application_by_id`/`get_application_works`/`ApplicationNotEditableError` (Tasks 7 & 9); `ArtistApplicationAdminOut`/`RejectRequest`/`ArtworkOut`/`ArtworkStatusUpdate` (Task 6); `get_current_admin_user` (`app/api/deps.py`, already exists)
- Produces: `list_artworks(db, status=None) -> list[Artwork]`, `get_artwork_by_id(db, artwork_id) -> Artwork | None`, `set_artwork_status(db, artwork, new_status) -> Artwork`, `InvalidArtworkStatusError` — consumed by Task 13's HTML panel.

- [ ] **Step 1: Create `app/services/artworks.py`**

```python
import uuid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.artwork import Artwork, ArtworkStatus


class InvalidArtworkStatusError(Exception):
    pass


async def list_artworks(db: AsyncSession, status: str | None = None) -> list[Artwork]:
    query = select(Artwork)
    if status is not None:
        query = query.where(Artwork.status == status)
    query = query.order_by(Artwork.created_at.desc())
    result = await db.execute(query)
    return list(result.scalars().all())


async def get_artwork_by_id(db: AsyncSession, artwork_id: uuid.UUID) -> Artwork | None:
    result = await db.execute(select(Artwork).where(Artwork.id == artwork_id))
    return result.scalars().first()


async def set_artwork_status(db: AsyncSession, artwork: Artwork, new_status: str) -> Artwork:
    valid_statuses = {s.value for s in ArtworkStatus}
    if new_status not in valid_statuses:
        raise InvalidArtworkStatusError(f"'{new_status}' is not a valid artwork status.")
    artwork.status = ArtworkStatus(new_status)
    await db.commit()
    await db.refresh(artwork)
    return artwork
```

- [ ] **Step 2: Create `app/api/v1/admin.py`**

```python
import uuid
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_admin_user
from app.db.session import get_db
from app.models.artist_application import ApplicationStatus
from app.models.user import User
from app.schemas.artist_application import ArtistApplicationAdminOut, RejectRequest
from app.schemas.artwork import ArtworkOut, ArtworkStatusUpdate
from app.services import artist_applications as applications_service
from app.services import artworks as artworks_service

router = APIRouter()


def _to_admin_out(application, works, applicant_email) -> dict:
    return {
        "id": application.id,
        "user_id": application.user_id,
        "applicant_email": applicant_email,
        "status": application.status,
        "full_name": application.full_name,
        "location": application.location,
        "primary_medium": application.primary_medium,
        "years_practising": application.years_practising,
        "website_url": application.website_url,
        "instagram": application.instagram,
        "statement": application.statement,
        "submitted_at": application.submitted_at,
        "reviewed_at": application.reviewed_at,
        "rejection_reason": application.rejection_reason,
        "works": [
            {
                "slot_index": w.slot_index,
                "title": w.title,
                "year": w.year,
                "medium": w.medium,
                "dimensions": w.dimensions,
                "image_url": w.image_url,
            }
            for w in works
        ],
    }


async def _get_application_or_404(db, application_id: uuid.UUID):
    application = await applications_service.get_application_by_id(db, application_id)
    if application is None:
        raise HTTPException(status_code=404, detail="Application not found")
    return application


async def _applicant_email(db, application) -> str:
    result = await db.execute(select(User.email).where(User.id == application.user_id))
    return result.scalar_one()


@router.get("/admin/applications", response_model=list[ArtistApplicationAdminOut])
async def list_applications_route(
    status: ApplicationStatus | None = Query(default=None),
    current_admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    rows = await applications_service.list_applications(db, status=status)
    return [_to_admin_out(app_, works, email) for app_, works, email in rows]


@router.post("/admin/applications/{application_id}/claim", response_model=ArtistApplicationAdminOut)
async def claim_application_route(
    application_id: uuid.UUID,
    current_admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    application = await _get_application_or_404(db, application_id)
    try:
        application = await applications_service.claim_application(db, application, current_admin)
    except applications_service.ApplicationNotEditableError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    works = await applications_service.get_application_works(db, application.id)
    email = await _applicant_email(db, application)
    return _to_admin_out(application, works, email)


@router.post("/admin/applications/{application_id}/approve", response_model=ArtistApplicationAdminOut)
async def approve_application_route(
    application_id: uuid.UUID,
    current_admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    application = await _get_application_or_404(db, application_id)
    try:
        await applications_service.approve_application(db, application, current_admin)
    except applications_service.ApplicationNotEditableError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    await db.refresh(application)
    works = await applications_service.get_application_works(db, application.id)
    email = await _applicant_email(db, application)
    return _to_admin_out(application, works, email)


@router.post("/admin/applications/{application_id}/reject", response_model=ArtistApplicationAdminOut)
async def reject_application_route(
    application_id: uuid.UUID,
    payload: RejectRequest,
    current_admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    application = await _get_application_or_404(db, application_id)
    try:
        application = await applications_service.reject_application(db, application, current_admin, payload.reason)
    except applications_service.ApplicationNotEditableError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    works = await applications_service.get_application_works(db, application.id)
    email = await _applicant_email(db, application)
    return _to_admin_out(application, works, email)


@router.get("/admin/artworks", response_model=list[ArtworkOut])
async def list_artworks_route(
    status: str | None = Query(default=None),
    current_admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    return await artworks_service.list_artworks(db, status=status)


@router.patch("/admin/artworks/{artwork_id}", response_model=ArtworkOut)
async def update_artwork_status_route(
    artwork_id: uuid.UUID,
    payload: ArtworkStatusUpdate,
    current_admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    artwork = await artworks_service.get_artwork_by_id(db, artwork_id)
    if artwork is None:
        raise HTTPException(status_code=404, detail="Artwork not found")
    try:
        artwork = await artworks_service.set_artwork_status(db, artwork, payload.status)
    except artworks_service.InvalidArtworkStatusError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return artwork
```

- [ ] **Step 3: Wire the router into `app/main.py`**

Update the import line to also pull in `admin`:
```python
from app.api.v1 import auth, artist_applications, admin
```
Add the include:
```python
app.include_router(admin.router, prefix=settings.API_V1_PREFIX, tags=["Admin"])
```

- [ ] **Step 4: Verify with `ast.parse`** on all three changed/new files

- [ ] **Step 5: Commit**

```bash
git add app/services/artworks.py app/api/v1/admin.py app/main.py
git commit -m "feat: add artworks service and admin JSON API"
```

---

### Task 11: Admin panel session auth + login page

**Files:**
- Create: `app/admin_panel/__init__.py`
- Create: `app/admin_panel/templates.py`
- Create: `app/admin_panel/auth.py`
- Create: `app/admin_panel/login_routes.py`
- Create: `app/admin_panel/templates/base.html`
- Create: `app/admin_panel/templates/login.html`
- Modify: `app/main.py`

**Interfaces:**
- Consumes: `verify_password` (`app/core/security.py`, already exists), `User` (`app/models/user.py`, already exists), `settings.SECRET_KEY`/`settings.cookie_secure` (`app/core/config.py`, already exists)
- Produces: `templates: Jinja2Templates` (Tasks 12 & 13), `AdminAuthRequired` (exception, registered as an app-level exception handler), `get_session_admin_user(request, db) -> User` (dependency, consumed by Tasks 12 & 13), `authenticate_admin(db, email, password) -> User | None`

**Design note:** This is a session-cookie login, entirely separate from the customer JWT/refresh-token system in `app/api/v1/auth.py` — different cookie name (`admin_session` vs `refresh_token`), different cookie path (`/admin` vs `/api/v1/auth`), no shared code path. `get_session_admin_user` re-reads `is_admin` from the DB on every request rather than trusting anything cached in the session — same principle as `get_current_user` in `app/api/deps.py` (PRD rule 26: never trust a cached permission).

- [ ] **Step 1: Create `app/admin_panel/__init__.py`** (empty file)

- [ ] **Step 2: Create `app/admin_panel/templates.py`**

```python
from pathlib import Path
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
```

- [ ] **Step 3: Create `app/admin_panel/auth.py`**

```python
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import Depends, Request

from app.core.security import verify_password
from app.db.session import get_db
from app.models.user import User


class AdminAuthRequired(Exception):
    """Raised by get_session_admin_user; app.main registers a handler that redirects to /admin/login."""


async def get_session_admin_user(request: Request, db: AsyncSession = Depends(get_db)) -> User:
    user_id = request.session.get("admin_user_id")
    if not user_id:
        raise AdminAuthRequired()
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalars().first()
    if user is None or not user.is_admin:
        request.session.clear()
        raise AdminAuthRequired()
    return user


async def authenticate_admin(db: AsyncSession, email: str, password: str) -> User | None:
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalars().first()
    if user is None or not user.password_hash or not user.is_admin:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user
```

- [ ] **Step 4: Create `app/admin_panel/login_routes.py`**

```python
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin_panel.auth import authenticate_admin
from app.admin_panel.templates import templates
from app.db.session import get_db

router = APIRouter()


@router.get("/login")
async def login_form(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login")
async def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    user = await authenticate_admin(db, email, password)
    if user is None:
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "Invalid email, password, or this account isn't an admin."},
            status_code=401,
        )
    request.session["admin_user_id"] = str(user.id)
    return RedirectResponse(url="/admin/applications", status_code=303)


@router.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/admin/login", status_code=303)
```

- [ ] **Step 5: Create `app/admin_panel/templates/base.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{% block title %}Picasso Admin{% endblock %}</title>
  <style>
    body { font-family: system-ui, sans-serif; margin: 2rem; color: #222; }
    table { border-collapse: collapse; width: 100%; margin-top: 1rem; }
    th, td { border: 1px solid #ccc; padding: 0.5rem 0.75rem; text-align: left; }
    th { background: #f0f0f0; }
    nav a { margin-right: 1rem; }
    .error { color: #b00020; }
    form.inline { display: inline; }
    input, textarea, select, button { font: inherit; padding: 0.4rem; }
    .badge { padding: 0.15rem 0.5rem; border-radius: 4px; background: #eee; font-size: 0.85em; }
  </style>
</head>
<body>
  {% block body %}{% endblock %}
</body>
</html>
```

- [ ] **Step 6: Create `app/admin_panel/templates/login.html`**

```html
{% extends "base.html" %}
{% block title %}Admin Login{% endblock %}
{% block body %}
  <h1>Picasso Admin</h1>
  {% if error %}<p class="error">{{ error }}</p>{% endif %}
  <form method="post" action="/admin/login">
    <p><label>Email <input type="email" name="email" required></label></p>
    <p><label>Password <input type="password" name="password" required></label></p>
    <button type="submit">Log in</button>
  </form>
{% endblock %}
```

- [ ] **Step 7: Wire session middleware, the exception handler, and the login router into `app/main.py`**

Add these imports:
```python
from starlette.middleware.sessions import SessionMiddleware
from fastapi.responses import RedirectResponse
from app.admin_panel.auth import AdminAuthRequired
from app.admin_panel import login_routes
```
Add the middleware registration (after `app = FastAPI(...)`, before any `include_router` calls):
```python
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.SECRET_KEY,
    session_cookie="admin_session",
    path="/admin",
    https_only=settings.cookie_secure,
)


@app.exception_handler(AdminAuthRequired)
async def admin_auth_required_handler(request, exc):
    return RedirectResponse(url="/admin/login", status_code=303)
```
Add the router include:
```python
app.include_router(login_routes.router, prefix="/admin", tags=["Admin Panel"])
```

- [ ] **Step 8: Verify with `ast.parse`** on all new/changed Python files

- [ ] **Step 9: Commit**

```bash
git add app/admin_panel/__init__.py app/admin_panel/templates.py app/admin_panel/auth.py app/admin_panel/login_routes.py app/admin_panel/templates/base.html app/admin_panel/templates/login.html app/main.py
git commit -m "feat: add admin panel session auth and login page"
```

---

### Task 12: Admin panel — applications pages

**Files:**
- Create: `app/admin_panel/application_routes.py`
- Create: `app/admin_panel/templates/applications_list.html`
- Create: `app/admin_panel/templates/application_detail.html`
- Modify: `app/main.py`

**Interfaces:**
- Consumes: `get_session_admin_user` (Task 11); `list_applications`, `get_application_by_id`, `get_application_works`, `approve_application`, `reject_application`, `ApplicationNotEditableError` (Tasks 7 & 9); `ApplicationStatus` (Task 3)

- [ ] **Step 1: Create `app/admin_panel/application_routes.py`**

```python
import uuid
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin_panel.auth import get_session_admin_user
from app.admin_panel.templates import templates
from app.db.session import get_db
from app.models.artist_application import ApplicationStatus
from app.models.user import User
from app.services import artist_applications as applications_service

router = APIRouter()


@router.get("/applications")
async def applications_list(
    request: Request,
    status: str | None = None,
    admin: User = Depends(get_session_admin_user),
    db: AsyncSession = Depends(get_db),
):
    status_filter = ApplicationStatus(status) if status else None
    rows = await applications_service.list_applications(db, status=status_filter)
    return templates.TemplateResponse(
        request,
        "applications_list.html",
        {"rows": rows, "current_status": status, "statuses": [s.value for s in ApplicationStatus]},
    )


@router.get("/applications/{application_id}")
async def application_detail(
    request: Request,
    application_id: uuid.UUID,
    admin: User = Depends(get_session_admin_user),
    db: AsyncSession = Depends(get_db),
):
    application = await applications_service.get_application_by_id(db, application_id)
    if application is None:
        return RedirectResponse(url="/admin/applications", status_code=303)
    works = await applications_service.get_application_works(db, application.id)
    result = await db.execute(select(User.email).where(User.id == application.user_id))
    applicant_email = result.scalar_one()
    return templates.TemplateResponse(
        request,
        "application_detail.html",
        {"application": application, "works": works, "applicant_email": applicant_email},
    )


@router.post("/applications/{application_id}/approve")
async def approve_application_form(
    application_id: uuid.UUID,
    admin: User = Depends(get_session_admin_user),
    db: AsyncSession = Depends(get_db),
):
    application = await applications_service.get_application_by_id(db, application_id)
    if application is not None:
        try:
            await applications_service.approve_application(db, application, admin)
        except applications_service.ApplicationNotEditableError:
            pass
    return RedirectResponse(url="/admin/applications", status_code=303)


@router.post("/applications/{application_id}/reject")
async def reject_application_form(
    application_id: uuid.UUID,
    reason: str = Form(...),
    admin: User = Depends(get_session_admin_user),
    db: AsyncSession = Depends(get_db),
):
    application = await applications_service.get_application_by_id(db, application_id)
    if application is not None:
        try:
            await applications_service.reject_application(db, application, admin, reason)
        except applications_service.ApplicationNotEditableError:
            pass
    return RedirectResponse(url="/admin/applications", status_code=303)
```

- [ ] **Step 2: Create `app/admin_panel/templates/applications_list.html`**

```html
{% extends "base.html" %}
{% block title %}Artist Applications{% endblock %}
{% block body %}
  <nav>
    <a href="/admin/applications">Applications</a><a href="/admin/artworks">Artworks</a>
    <form class="inline" method="post" action="/admin/logout"><button type="submit">Log out</button></form>
  </nav>
  <h1>Artist Applications</h1>
  <form method="get">
    <select name="status" onchange="this.form.submit()">
      <option value="">All statuses</option>
      {% for s in statuses %}
        <option value="{{ s }}" {% if current_status == s %}selected{% endif %}>{{ s }}</option>
      {% endfor %}
    </select>
  </form>
  <table>
    <tr><th>Applicant</th><th>Medium</th><th>Status</th><th>Submitted</th><th></th></tr>
    {% for application, works, email in rows %}
      <tr>
        <td>{{ application.full_name }} ({{ email }})</td>
        <td>{{ application.primary_medium }}</td>
        <td><span class="badge">{{ application.status.value }}</span></td>
        <td>{{ application.submitted_at or "—" }}</td>
        <td><a href="/admin/applications/{{ application.id }}">Review</a></td>
      </tr>
    {% endfor %}
  </table>
{% endblock %}
```

- [ ] **Step 3: Create `app/admin_panel/templates/application_detail.html`**

```html
{% extends "base.html" %}
{% block title %}Application — {{ application.full_name }}{% endblock %}
{% block body %}
  <nav><a href="/admin/applications">&larr; Back to applications</a></nav>
  <h1>{{ application.full_name }}</h1>
  <p><strong>Email:</strong> {{ applicant_email }}</p>
  <p><strong>Location:</strong> {{ application.location }}</p>
  <p><strong>Primary medium:</strong> {{ application.primary_medium }}</p>
  <p><strong>Years practising:</strong> {{ application.years_practising or "—" }}</p>
  <p><strong>Statement:</strong> {{ application.statement or "—" }}</p>
  <p><strong>Status:</strong> <span class="badge">{{ application.status.value }}</span></p>
  {% if application.rejection_reason %}
    <p><strong>Rejection reason:</strong> {{ application.rejection_reason }}</p>
  {% endif %}

  <h2>Submitted works</h2>
  <table>
    <tr><th>#</th><th>Title</th><th>Year</th><th>Medium</th><th>Dimensions</th><th>Image</th></tr>
    {% for work in works %}
      <tr>
        <td>{{ work.slot_index + 1 }}</td>
        <td>{{ work.title }}</td>
        <td>{{ work.year or "—" }}</td>
        <td>{{ work.medium or "—" }}</td>
        <td>{{ work.dimensions or "—" }}</td>
        <td><a href="{{ work.image_url }}" target="_blank">view</a></td>
      </tr>
    {% endfor %}
  </table>

  {% if application.status.value in ("submitted", "under_review") %}
    <h2>Decision</h2>
    <form class="inline" method="post" action="/admin/applications/{{ application.id }}/approve">
      <button type="submit">Approve</button>
    </form>
    <form method="post" action="/admin/applications/{{ application.id }}/reject">
      <textarea name="reason" placeholder="Reason for rejection" required rows="3" cols="40"></textarea><br>
      <button type="submit">Reject</button>
    </form>
  {% endif %}
{% endblock %}
```

- [ ] **Step 4: Wire the router into `app/main.py`**

Add the import: `from app.admin_panel import application_routes` (alongside the `login_routes` import).
Add the include: `app.include_router(application_routes.router, prefix="/admin", tags=["Admin Panel"])`

- [ ] **Step 5: Verify with `ast.parse`** on `app/admin_panel/application_routes.py` and `app/main.py`

- [ ] **Step 6: Commit**

```bash
git add app/admin_panel/application_routes.py app/admin_panel/templates/applications_list.html app/admin_panel/templates/application_detail.html app/main.py
git commit -m "feat: add admin panel applications list/detail pages"
```

---

### Task 13: Admin panel — artworks page

**Files:**
- Create: `app/admin_panel/artwork_routes.py`
- Create: `app/admin_panel/templates/artworks_list.html`
- Modify: `app/main.py`

**Interfaces:**
- Consumes: `get_session_admin_user` (Task 11); `list_artworks`, `get_artwork_by_id`, `set_artwork_status`, `InvalidArtworkStatusError` (Task 10); `ArtworkStatus` (Task 5)

- [ ] **Step 1: Create `app/admin_panel/artwork_routes.py`**

```python
import uuid
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin_panel.auth import get_session_admin_user
from app.admin_panel.templates import templates
from app.db.session import get_db
from app.models.artwork import ArtworkStatus
from app.models.user import User
from app.services import artworks as artworks_service

router = APIRouter()


@router.get("/artworks")
async def artworks_list(
    request: Request,
    status: str | None = None,
    admin: User = Depends(get_session_admin_user),
    db: AsyncSession = Depends(get_db),
):
    artworks = await artworks_service.list_artworks(db, status=status)
    return templates.TemplateResponse(
        request,
        "artworks_list.html",
        {"artworks": artworks, "current_status": status, "statuses": [s.value for s in ArtworkStatus]},
    )


@router.post("/artworks/{artwork_id}/status")
async def update_artwork_status_form(
    artwork_id: uuid.UUID,
    new_status: str = Form(...),
    admin: User = Depends(get_session_admin_user),
    db: AsyncSession = Depends(get_db),
):
    artwork = await artworks_service.get_artwork_by_id(db, artwork_id)
    if artwork is not None:
        try:
            await artworks_service.set_artwork_status(db, artwork, new_status)
        except artworks_service.InvalidArtworkStatusError:
            pass
    return RedirectResponse(url="/admin/artworks", status_code=303)
```

- [ ] **Step 2: Create `app/admin_panel/templates/artworks_list.html`**

```html
{% extends "base.html" %}
{% block title %}Artworks{% endblock %}
{% block body %}
  <nav>
    <a href="/admin/applications">Applications</a><a href="/admin/artworks">Artworks</a>
    <form class="inline" method="post" action="/admin/logout"><button type="submit">Log out</button></form>
  </nav>
  <h1>Artworks</h1>
  <form method="get">
    <select name="status" onchange="this.form.submit()">
      <option value="">All statuses</option>
      {% for s in statuses %}
        <option value="{{ s }}" {% if current_status == s %}selected{% endif %}>{{ s }}</option>
      {% endfor %}
    </select>
  </form>
  <table>
    <tr><th>Title</th><th>Listing type</th><th>Status</th><th>Change status</th></tr>
    {% for artwork in artworks %}
      <tr>
        <td>{{ artwork.title }}</td>
        <td>{{ artwork.listing_type.value }}</td>
        <td><span class="badge">{{ artwork.status.value }}</span></td>
        <td>
          <form class="inline" method="post" action="/admin/artworks/{{ artwork.id }}/status">
            <select name="new_status">
              {% for s in statuses %}
                <option value="{{ s }}" {% if artwork.status.value == s %}selected{% endif %}>{{ s }}</option>
              {% endfor %}
            </select>
            <button type="submit">Update</button>
          </form>
        </td>
      </tr>
    {% endfor %}
  </table>
{% endblock %}
```

- [ ] **Step 3: Wire the router into `app/main.py`**

Add the import: `from app.admin_panel import artwork_routes` (alongside the other `app.admin_panel` imports).
Add the include: `app.include_router(artwork_routes.router, prefix="/admin", tags=["Admin Panel"])`

- [ ] **Step 4: Verify with `ast.parse`** on `app/admin_panel/artwork_routes.py` and `app/main.py`

- [ ] **Step 5: Commit**

```bash
git add app/admin_panel/artwork_routes.py app/admin_panel/templates/artworks_list.html app/main.py
git commit -m "feat: add admin panel artworks moderation page"
```

---

### Task 14: Final wiring check, live smoke test, and docs update

**Files:**
- Modify: `docs/API_REFERENCE.md`

**Interfaces:**
- Consumes: everything from Tasks 1–13, deployed.

- [ ] **Step 1: Full local sanity pass before pushing**

Run: `python -c "import ast, pathlib; [ast.parse(p.read_text()) for p in pathlib.Path('app').rglob('*.py')]; print('OK')"`
Expected: `OK` (catches anything a per-task check might have missed after later edits touched earlier files).

Run: `python -m pytest tests/ -v`
Expected: all pure-helper tests from Task 2 still pass.

Run (same as every migration task): `POSTGRES_SERVER=localhost POSTGRES_USER=x POSTGRES_PASSWORD=x POSTGRES_DB=x SECRET_KEY=test python -m alembic upgrade head --sql`
Expected: all three new migrations' SQL present, no errors, ends cleanly.

- [ ] **Step 2: Push and open the PR, wait for Railway to redeploy**

Standard flow already used earlier in this project's history: push the branch, open a PR against `main`, merge, then confirm `railway status` shows the service back to `● Online` with a new deployment ID.

- [ ] **Step 3: Live smoke test — full artist-application lifecycle**

Against the deployed URL, in order (values illustrative):
1. `POST /api/v1/auth/register` a fresh test user → save `access_token`.
2. `POST /api/v1/me/artist-application` with `{"full_name": "...", "location": "...", "primary_medium": "..."}` → expect `201`/`200` with `status: "draft"`.
3. `PUT /api/v1/me/artist-application/works/0`, `/1`, `/2`, each with `{"title": "...", "image_url": "https://..."}` → expect `200` each time.
4. `POST /api/v1/me/artist-application/submit` **before** step 3 completes all three slots (test on a second fresh application) → expect `422` with body `{"detail": {"works": ["Submit three works…"]}}`.
5. `POST /api/v1/me/artist-application/submit` after all three slots are filled → expect `200`, `status: "submitted"`; then `GET /api/v1/auth/me` on that user → expect `artist_status: "pending"`.
6. Log into `/admin/login` in a browser with the admin credentials already set up on this project; confirm the applications list shows the new submission.
7. Click into the application detail page, confirm all three works render with working image links, click Approve.
8. `GET /api/v1/auth/me` on the applicant user again → expect `artist_status: "approved"`.
9. `GET /api/v1/admin/artworks` (as the admin, Bearer token) → expect 3 new artworks with `status: "draft"`, `listing_type: "display"`.
10. On `/admin/artworks` in the browser, change one artwork's status to `published`, confirm the table reflects it and a repeat `GET /api/v1/admin/artworks?status=published` returns it.
11. Submit a third test application, reject it from the admin panel with a reason, confirm `GET /api/v1/auth/me` shows `artist_status: "rejected"` and the applicant's `POST /me/artist-application/submit` retry within 30 days returns `422` with the reapply-date message from Task 8.

- [ ] **Step 4: Update `docs/API_REFERENCE.md`**

Move every endpoint built in this plan from the "🚧 not built yet" section into the "✅ implemented" table (`/me/artist-application*`, `/admin/applications*`, `/admin/artworks*`), and add a new section documenting the `/admin/*` HTML panel (URL, that it's session-cookie authenticated separately from the JWT/refresh-token API, and that there's no self-service way to grant `is_admin` — still a direct DB operation).

- [ ] **Step 5: Commit and push**

```bash
git add docs/API_REFERENCE.md
git commit -m "docs: mark artist-application and admin endpoints as implemented"
git push
```

---

## Self-Review Notes

- **Spec coverage:** every section of the design spec has a task — §3 data model → Tasks 3–5; §4 service layer → Tasks 7–10 (split further for right-sizing); §5 JSON API → Tasks 7, 8, 10; §6 admin panel → Tasks 11–13; §7 error handling → addressed inline in Task 8's design note; §8 testing → Task 2 (pure unit tests) + Task 14 (live smoke test); §9 migration order → Tasks 3, 4, 5 in that order.
- **Placeholder scan:** no TBD/TODO markers; every step has real, complete code.
- **Type consistency check:** `ApplicationNotEditableError`, `ApplicationValidationError`, `InvalidArtworkStatusError`, `AdminAuthRequired` are each defined exactly once (Tasks 7, 8, 10, 11 respectively) and imported by name everywhere else they're used. `get_current_admin_user` (customer-JWT admin check) and `get_session_admin_user` (admin-panel session check) are intentionally two different dependencies for two different auth systems — not a naming inconsistency.
- **One deviation from the merged spec, called out explicitly in Task 7:** `GET /me/artist-application` 404s instead of auto-creating an empty draft, because `full_name`/`location`/`primary_medium` are `NOT NULL` per PRD §3.4 — an empty draft is not insertable. Surfaced here again in case it should go back to the user for confirmation before Task 7 starts.

