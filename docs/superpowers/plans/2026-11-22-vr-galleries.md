# VR Galleries (vendor + first-party) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the backend for a list of VR galleries (`exhibitions`), each either a vendor embed or a first-party 3D room generated from assigned artworks, with public read endpoints and an HTML admin panel to manage them.

**Architecture:** Extend the PRD §3.8 `exhibitions` / `exhibition_artworks` tables with `viewer_kind` and `sort_order`. Business rules live in `app/services/exhibition_admin.py` (writes) and `app/services/exhibitions.py` (public reads); routes stay thin. Admin is a server-rendered Jinja panel like artworks/users. The 3D viewer is out of scope here (frontend issue, Task 5).

**Tech Stack:** FastAPI, SQLAlchemy 2.0 async, Alembic, Pydantic v2, Jinja2 admin templates, pytest (SQLite `DbHarness` for DB-backed tests).

**Spec:** `docs/superpowers/specs/2026-11-22-vr-galleries-design.md`

## Global Constraints

- Resource and paths stay `exhibitions`: `GET /api/v1/exhibitions`, `GET /api/v1/exhibitions/{slug}` (PRD §4.2). Admin pages at `/admin/exhibitions*` (HTML, `include_in_schema=False`).
- `viewer_kind TEXT NOT NULL`, `CHECK (viewer_kind IN ('vendor_embed','first_party'))`; `CHECK (viewer_kind <> 'vendor_embed' OR vr_tour_url IS NOT NULL)`; `sort_order SMALLINT NOT NULL DEFAULT 0`.
- Do not simplify away CHECK constraints or the composite PK (CLAUDE.md).
- Money is `str` of NUMERIC(12,2), never float. Timestamps ISO 8601 UTC.
- A `first_party` exhibition cannot be published with zero assigned artworks; unpublishing is always allowed.
- Order of artworks in a room: `wall_position` ascending, NULLs last, then `artwork_id`.
- The API returns artwork `id`/`slug`; the frontend builds the buy-page URL. No buy link field.
- Only artworks with status in `published, reserved, sold` are shown in public exhibitions.
- Unknown or unpublished slug: `404 {"detail": "Exhibition not found"}`.
- Any API change: run `python scripts/export_openapi.py` and update `docs/API_REFERENCE.md` in the same PR.

## Review Focus

Failure modes the spec implies but its tasks would not otherwise test (each has a test in the owning task):

1. `vr_tour_url` of `javascript:`/`data:`/non-http(s) scheme would be rendered into a frontend iframe: reject in admin validation (Task 2).
2. Switching a **published** gallery from `vendor_embed` to `first_party` with no artworks must be refused, not silently left public and empty (Task 2).
3. An assigned artwork that is later `draft`/`removed` must vanish from the public room without breaking the response (Task 3).
4. A `vendor_embed` response must never leak an `artworks` list and a `first_party` response must never expose `vr_tour_url` (Task 3).
5. Logged-out callers must not be able to bulk-read the catalogue through a gallery: first-party artworks for anonymous callers are limited to the teaser set, matching `/artworks` (Task 3). **Product call, see Task 3 note.**

---

### Task 1: Models, migration, harness tables

**Files:**
- Create: `app/models/exhibition.py`, `migrations/versions/f7a9c1e3b5d2_create_exhibitions.py`
- Modify: `app/models/__init__.py`, `tests/conftest.py` (add the two tables to `DbHarness.tables`)
- Test: `tests/test_exhibition_models.py`

**Interfaces:**
- Produces: `class ViewerKind(str, enum.Enum)` with `vendor_embed`, `first_party`; `class Exhibition(Base)` (table `exhibitions`; columns per PRD §3.8 plus `viewer_kind: Mapped[str]`, `sort_order: Mapped[int]`, `created_at`, `updated_at`); `class ExhibitionArtwork(Base)` (table `exhibition_artworks`, composite PK `(exhibition_id, artwork_id)`, `wall_position: Mapped[int | None]`, both FKs `ondelete="CASCADE"`).

- [ ] **Step 1: Write failing tests** in `tests/test_exhibition_models.py` using the `harness` fixture. Names and assertions:
  - `test_vendor_embed_requires_vr_tour_url`: inserting `Exhibition(viewer_kind="vendor_embed", vr_tour_url=None, ...)` raises `IntegrityError` on commit.
  - `test_first_party_does_not_need_vr_tour_url`: insert succeeds.
  - `test_unknown_viewer_kind_rejected`: `viewer_kind="hologram"` raises `IntegrityError`.
  - `test_slug_unique`: two rows with the same slug raise `IntegrityError`.
  - `test_artwork_assigned_once_per_exhibition`: duplicate `(exhibition_id, artwork_id)` raises `IntegrityError`.
- [ ] **Step 2: Run** `pytest tests/test_exhibition_models.py -v`. Expected: FAIL (import error).
- [ ] **Step 3: Implement** both models in `app/models/exhibition.py`. Use `Text` + `CheckConstraint` for `viewer_kind` (no native PG enum), `Date` for `starts_on`/`ends_on`, `curator_id` FK `users.id` nullable, `is_published` default false. Register in `app/models/__init__.py`. Add `Exhibition.__table__` and `ExhibitionArtwork.__table__` to the `tables` list in `DbHarness.__init__`.
- [ ] **Step 4: Write the Alembic migration** with `down_revision = 'a1b2c3d4e5f6'` (confirm with `alembic heads` first; if it differs, use the actual head). Mirror the style of `b7d3e1f9a2c6_create_testimonials.py`; include both CHECKs, the unique slug, and `downgrade()` dropping `exhibition_artworks` then `exhibitions`.
- [ ] **Step 5: Run** `pytest tests/test_exhibition_models.py -v` (PASS) and `alembic heads` (exactly one head, the new revision).
- [ ] **Step 6: Commit** `feat: exhibitions and exhibition_artworks tables with viewer_kind`.

---

### Task 2: Admin write service (validation and rules)

**Files:**
- Create: `app/services/exhibition_admin.py`
- Test: `tests/test_exhibition_admin_service.py`

**Interfaces:**
- Consumes: `Exhibition`, `ExhibitionArtwork`, `ViewerKind` (Task 1); `generate_unique_slug` from `app/services/slugs.py`.
- Produces (all `async`, take `db: AsyncSession` first):
  - `class ExhibitionValidationError(Exception)` with `.errors: dict[str, str]` (same shape as `ArtworkValidationError`).
  - `class ExhibitionNotFoundError(Exception)`.
  - `clean_exhibition_form(raw: dict, existing: Exhibition | None = None) -> dict` (sync, no DB): validates title (required, ≤200), `viewer_kind`, `vr_tour_url` (required for vendor_embed; must start with `https://` or `http://`; message `"Enter a full web address starting with https://."`), `sort_order` int 0..32767, `starts_on`/`ends_on` ISO dates.
  - `create_exhibition(db, clean: dict) -> Exhibition`: generates a unique slug from the title; always created unpublished.
  - `update_exhibition(db, exhibition_id: uuid.UUID, clean: dict) -> Exhibition`: slug is not regenerated; if the row is published, re-applies the publish rule.
  - `set_published(db, exhibition_id: uuid.UUID, published: bool) -> Exhibition`.
  - `set_artworks(db, exhibition_id: uuid.UUID, positions: dict[uuid.UUID, int | None]) -> None`: replaces the full assignment; unknown artwork ids raise `ExhibitionValidationError({"artworks": "..."})`; positions must be unique among non-null values.
  - `delete_exhibition(db, exhibition_id: uuid.UUID) -> None`.
  - `PUBLISH_NEEDS_ARTWORKS = "Add at least one artwork before publishing a first-party gallery."`

- [ ] **Step 1: Write failing tests** (harness-backed; helper creates exhibitions with `h.make_artist`/`h.make_artwork`):
  - `test_vendor_url_scheme_rejected`: `javascript:alert(1)`, `data:text/html,x`, `ftp://x` each yield `errors["vr_tour_url"]`; `https://my.artsteps.com/x` passes.
  - `test_vendor_embed_requires_url`; `test_first_party_ignores_blank_url`.
  - `test_create_generates_unique_slug_and_unpublished`: two galleries titled "Spring Show" get `spring-show`, `spring-show-2`.
  - `test_publish_first_party_without_artworks_refused`: raises `ExhibitionValidationError` with `PUBLISH_NEEDS_ARTWORKS`.
  - `test_publish_vendor_without_artworks_allowed`.
  - `test_unpublish_always_allowed`.
  - `test_update_published_vendor_to_first_party_without_artworks_refused` (Review Focus 2); the row stays `vendor_embed` and published afterwards.
  - `test_set_artworks_replaces_assignment_and_rejects_duplicate_positions`; `test_set_artworks_unknown_artwork_rejected`.
  - `test_delete_removes_assignments` (assignments gone, artworks untouched).
- [ ] **Step 2: Run** `pytest tests/test_exhibition_admin_service.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** the functions above. Follow `app/services/artwork_admin.py` for error-dict style and `_text` helper conventions; translate `IntegrityError` on slug races into a retry with the next suffix.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** `feat: exhibition admin service with publish and URL rules`.

---

### Task 3: Public read service, schemas, endpoints

**Files:**
- Create: `app/services/exhibitions.py`, `app/schemas/exhibition.py`, `app/api/v1/exhibitions.py`
- Modify: `app/services/catalogue.py` (rename `_artwork_card_dict` to `artwork_card_dict`, update its call sites in that file; no other file uses it), `app/main.py` (include router with `tags=["Exhibitions"]`)
- Test: `tests/test_exhibitions_api.py`

**Interfaces:**
- Consumes: models (Task 1); `artwork_card_dict(artwork, artist_slug, artist_display_name) -> dict`; `get_teaser_artwork_ids(db) -> set[UUID]`; `get_optional_user`.
- Produces:
  - `list_published_exhibitions(db) -> list[dict]`: keys `id, title, slug, cover_image_url, viewer_kind`, ordered by `sort_order, id`.
  - `get_published_exhibition(db, slug: str, only_ids: set[uuid.UUID] | None = None) -> dict | None`: the list keys plus `description`, `starts_on`, `ends_on`, and either `vr_tour_url` (vendor_embed) or `artworks` (first_party). Each artwork is the catalogue card dict plus `wall_position`, `width_cm`, `height_cm` (strings or None), `texture_url` (primary image `preview_url`, falling back to `url`, else `primary_image_url`), `image_width_px`, `image_height_px`.
  - Schemas: `ExhibitionCardOut`, `ExhibitionArtworkOut(ArtworkCardOut)`, `ExhibitionDetailOut` with optional `vr_tour_url: str | None`, `artworks: list[ExhibitionArtworkOut] | None`, `teaser: bool`; `ExhibitionsOut {items: list[ExhibitionCardOut]}`.
  - Routes `GET /exhibitions` (no auth, no teaser) and `GET /exhibitions/{slug}` (anonymous: `only_ids = teaser set` and `teaser: true`; logged in: all, `teaser: false`).

> **Product call (needs your OK):** applying the teaser to first-party rooms means a logged-out visitor sees only teaser-set artworks inside a gallery. That matches the existing server-enforced rule ("logged-out visitors get a teaser") and stops the gallery being a back door to the full catalogue, but rooms will look sparse when logged out. If you would rather show whole galleries to everyone, this is a one-line change (`only_ids=None`).

- [ ] **Step 1: Write failing tests** (harness-backed with `get_db` override, `TestClient`):
  - `test_list_only_published_ordered_by_sort_order`; response keys exactly `{"id","title","slug","cover_image_url","viewer_kind"}`; no auth needed.
  - `test_unknown_and_unpublished_slug_404` with `{"detail": "Exhibition not found"}`.
  - `test_vendor_detail_has_url_and_no_artworks` and `test_first_party_detail_has_artworks_and_no_url` (Review Focus 4: assert key absence in the JSON).
  - `test_artworks_ordered_by_wall_position_nulls_last`.
  - `test_non_public_artworks_excluded` (draft and removed assigned artworks absent; Review Focus 3).
  - `test_price_is_string_and_sold_status_present` (`"price": "1200.00"`, `status` and `sold` fields).
  - `test_anonymous_gets_teaser_subset_and_flag`: seed more than `TEASER_LIMIT` public artworks, assign all; anonymous response has `teaser: true` and only teaser ids; with `get_optional_user` overridden to a user object, all are returned and `teaser: false`.
  - `test_texture_url_prefers_preview`.
- [ ] **Step 2: Run** `pytest tests/test_exhibitions_api.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** the rename, service, schemas, router and `main.py` include. One query for the exhibition, one joined query for artworks with primary `ArtworkImage` (outer join on `is_primary`).
- [ ] **Step 4: Run** `pytest tests/test_exhibitions_api.py tests/test_catalogue_gating_http.py tests/test_teaser.py -v`. Expected: PASS (catalogue unaffected by the rename).
- [ ] **Step 5: Commit** `feat: public exhibitions endpoints (vendor embed and first-party artworks)`.

---

### Task 4: HTML admin panel

**Files:**
- Create: `app/admin_panel/exhibition_routes.py`, templates `exhibitions_list.html`, `exhibition_form.html`, `exhibition_delete.html` (in `app/admin_panel/templates/`)
- Modify: `app/main.py` (include router at prefix `/admin`, `include_in_schema=False`), `app/admin_panel/templates/base.html` (nav link "Galleries" beside the existing links)
- Test: `tests/test_admin_exhibitions_panel.py`

**Interfaces:**
- Consumes: Task 2 service; `get_session_admin_user`, `verify_csrf`, `templates` (same imports as `artwork_routes.py`); `artwork_admin.list_artist_choices` pattern for choosing artworks.
- Produces routes: `GET /admin/exhibitions`, `GET|POST /admin/exhibitions/new`, `GET|POST /admin/exhibitions/{id}/edit`, `POST /admin/exhibitions/{id}/artworks` (assignment and positions), `POST /admin/exhibitions/{id}/publish` and `/unpublish`, `GET|POST /admin/exhibitions/{id}/delete`. Validation errors re-render the form with 422 or redirect with `?error=` like `artwork_routes.py`; create redirects to the edit page.

- [ ] **Step 1: Write failing tests** modelled on `tests/test_admin_artworks_panel.py` (same `panel` fixture pattern with `get_session_admin_user` overridden):
  - `test_pages_require_login` (303 to `/admin/login`).
  - `test_create_vendor_gallery_requires_url_and_shows_field_error`.
  - `test_create_first_party_then_assign_artworks_then_publish`.
  - `test_publish_empty_first_party_shows_service_message` (`PUBLISH_NEEDS_ARTWORKS` in the redirect `error`).
  - `test_post_without_csrf_token_is_rejected`.
  - `test_delete_requires_confirm_page_then_post`.
- [ ] **Step 2: Run** `pytest tests/test_admin_exhibitions_panel.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** routes and templates by following `artwork_routes.py` and `artwork_form.html` (copy the layout, flash notice/error and csrf hidden field). The form shows the vendor URL field and the artwork-assignment block conditionally on `viewer_kind` with plain server-side rendering (no JS requirement).
- [ ] **Step 4: Run** the new tests plus `pytest tests/test_admin_artworks_panel.py tests/test_user_admin.py -v`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: admin panel for managing VR galleries`.

---

### Task 5: Contract, docs, seed data, frontend issue

**Files:**
- Modify: `docs/openapi.json` (generated), `docs/API_REFERENCE.md` (§1 table row, new "Exhibitions" subsection in §3 after Testimonials, §4 Phase 6 item 22, §5 line about `/exhibitions`), `docs/PRD.md` (§1 non-goals: drop "first-party VR rendering"; §3.8: add `viewer_kind`, `sort_order`; §4.2 note; §8 decision #6 marked resolved: per-gallery kind), `scripts/seed_dev_data.py` (one vendor gallery with a placeholder `https://` URL, one first-party gallery assigned to existing seed artworks; wire into `scripts/seed_dev.py` and extend `tests/test_seed_dev.py` accordingly)
- Create: `docs/superpowers/frontend-issue-vr-galleries.md` (issue body)
- Test: `tests/test_openapi_contract.py`, `tests/test_seed_dev.py`

- [ ] **Step 1: Run** `python scripts/export_openapi.py`; confirm `docs/openapi.json` now lists `/api/v1/exhibitions` and `/api/v1/exhibitions/{slug}` and nothing under `/admin/exhibitions`.
- [ ] **Step 2: Update** the docs listed above. API_REFERENCE must document both response shapes (one example per `viewer_kind`), the teaser behaviour, the 404 copy, and that the frontend builds buy links from `slug`.
- [ ] **Step 3: Extend seed data** and its test; run `pytest tests/test_seed_dev.py -v`.
- [ ] **Step 4: Write the frontend issue body** from the spec's "Frontend design" section (registry layout, kind-agnostic list/`[slug]` pages, lazy-loaded first-party viewer, fallback for unknown `viewer_kind`, labels `needs-frontend`, `contract-change`). **Ask the product owner before running** `gh issue create --repo mekriha-tech/picasso-frontend` (publishes outside this repo).
- [ ] **Step 5: Run the full suite** `pytest -q`. Expected: all pass, including `tests/test_openapi_contract.py`.
- [ ] **Step 6: Commit** `docs: exhibitions API reference, PRD decision #6, dev seed galleries`.

---

## Self-review

- **Spec coverage:** per-gallery `viewer_kind` + constraints (T1); `sort_order`, publish rule, vendor URL (T2); list/detail endpoints, ordering, no buy link, price strings (T3); admin CRUD (T4); openapi, API_REFERENCE, PRD §1/§3.8/§8 updates, frontend issue (T5). Frontend viewer and registry are specified in the issue, not built here, per the spec's sequencing.
- **Types:** `ViewerKind`, `Exhibition`, `ExhibitionArtwork`, `clean_exhibition_form`, `set_published`, `set_artworks`, `get_published_exhibition` are named identically wherever used.
- **Added beyond the spec (my calls):** `texture_url`, `image_width_px`/`image_height_px` (the room needs aspect ratio and a lighter texture), the teaser gating of first-party artworks (flagged in Task 3), `http(s)` scheme validation for the vendor URL.
