# VR galleries: vendor and first-party side by side — design

Status: draft, awaiting product-owner approval. Date: 2026-11-22.

## Intent and why

Today the VR gallery is a vendor-provided space (Artsteps) shown in an iframe. Two limits:
buyers cannot purchase from inside the gallery (or even be sent to that artwork's buy page),
and the vendor charges per gallery, which caps how many galleries Picasso can have.

We will build a first-party 3D gallery that costs nothing per gallery and can sell, **without
removing the vendor galleries**. The product owner wants to explore both before deciding to keep
one or both. Resolves PRD §8 decision #6 ("stay on Artsteps embeds, or build first-party rooms")
as "both, per gallery", and replaces the PRD §1 non-goal "first-party VR rendering".

## What the visitor sees

- A galleries page lists every published gallery as a button: "Visit VR gallery 1",
  "Visit VR gallery 2", and so on. Today that is one vendor gallery and one first-party gallery.
- Opening a **vendor** gallery shows the existing iframe, unchanged.
- Opening a **first-party** gallery loads a 3D room. The visitor walks around freely (mouse-look
  plus WASD on desktop, touch controls on mobile). Clicking a painting opens a panel with title,
  artist, price and sold status, plus a Buy link to that artwork's buy page.

## Goals

- A list of galleries, each either `vendor_embed` or `first_party`, mixed freely.
- First-party rooms are **generated from data** (artworks plus `wall_position`), not hand-modelled.
  Creating a new first-party gallery is an admin data task, with no code change.
- Both kinds appear in the same list with the same button treatment.

## Non-goals

- In-scene checkout. Buying is a link to the artwork's existing buy page. Orders are not built
  yet (PRD §7), so this also avoids depending on them.
- Hand-modelled or multiple room templates (possible later; the viewer should leave room for it).
- Migrating or removing vendor galleries. Choosing to keep one or both is a later decision that
  should need no data migration (hide a kind or unpublish galleries).

## Anti-goals (a failure even if it works)

- Per-gallery code in the frontend. Adding the tenth gallery must never require a frontend code
  change, and adding a new *kind* of viewer must touch one folder plus one registry line.
- Gallery-kind logic leaking into generic pages (list, routing, SEO, buttons).
- Simplifying away the schema's constraints (project rule).

## Approaches considered

How the two kinds coexist:

- **A. Both viewers on the same exhibition**, with a switch. Good for comparing identical content,
  but the product owner wants a list of galleries of mixed kinds, so this does not fit.
- **B. Per-gallery `viewer_kind` (chosen).** Each gallery is one kind. Matches "a list, some
  vendor, some self-built". Comparison is done by making two galleries with the same artworks.
- **C. Site-wide flag.** Cannot mix kinds. Rejected.

How the 3D room is built: **react-three-fiber + drei** (chosen: native to Next, lazy-loadable,
pointer-lock controls available). Plain Three.js (more boilerplate, awkward with React) and
Babylon.js (heavier, less natural in a React app) were rejected.

Layout: **generated from data** (chosen over a few templates or hand-modelled rooms).

## Backend design (this repo)

The resource stays `exhibitions` (PRD §3.8, §4: the frontend is built against `/exhibitions`).
"VR gallery" is the product name for an exhibition.

Schema changes to `exhibitions` (new Alembic migration; follow the PRD's constraint style):

- `viewer_kind TEXT NOT NULL` with `CHECK (viewer_kind IN ('vendor_embed','first_party'))`.
- `CHECK (viewer_kind <> 'vendor_embed' OR vr_tour_url IS NOT NULL)`: a vendor gallery must have
  its embed URL. (`vr_tour_url` keeps its current meaning.)
- `sort_order SMALLINT NOT NULL DEFAULT 0`: button order. *My call.*
- Service-layer rule: a `first_party` gallery cannot be published with zero artworks.
  Unpublishing is always allowed. *My call.*
- `exhibition_artworks.wall_position`: keep as is. Order within the room follows it (nulls last).

Endpoints (PRD §4.x `GET /exhibitions`, `GET /exhibitions/{slug}`; match the PRD's documented
paths, methods and status codes, and reuse §4.6 error copy where it applies):

- `GET /exhibitions`: published galleries ordered by `sort_order`, each with id, title, slug,
  cover image, `viewer_kind`.
- `GET /exhibitions/{slug}`: the above plus, depending on kind:
  - `vendor_embed`: `vr_tour_url`.
  - `first_party`: `artworks[]`, each with id, slug, title, artist name, image URL, dimensions if
    known, price (NUMERIC as string, per project money rules), sale status, `wall_position`.
    The API returns artwork id/slug; **the frontend builds the buy-page URL** (routes are its
    concern). *My call, correcting my earlier suggestion that the API returns a link.*
- Admin CRUD `/admin/exhibitions` (already listed in PRD §4) covers `viewer_kind`, `sort_order` and
  artwork assignment/ordering.
- Business rules live in a service module, not route handlers. Regenerate `docs/openapi.json` and
  update `docs/API_REFERENCE.md` and PRD (§1 non-goal, §3.8, §8 #6) in the same PR.

## Frontend design (picasso-frontend, via a `needs-frontend` issue)

Organization is a requirement, not a nicety:

```
features/galleries/
  registry.ts            kind -> lazy viewer component   (the ONLY place kinds are listed)
  GalleryList.tsx        buttons from GET /exhibitions    (kind-agnostic)
  GalleryPage.tsx        fetch by slug, look up registry  (kind-agnostic)
  viewers/
    vendor-embed/        iframe wrapper, nothing else
    first-party/
      Scene/             room generation from the artworks array
      Controls/          pointer-lock + WASD, touch joystick
      Hud/               painting panel (price, status, Buy link)
```

- Routes: one list page and one `[slug]` page. Neither knows about specific kinds.
- The first-party viewer is dynamically imported, so the 3D code is only downloaded when opened.
- Adding a gallery = admin data only. Adding a viewer kind = new folder + one registry entry.
- Unknown `viewer_kind` from the API renders a safe fallback (not a crash).

Room generation direction (details left to the builder): a rectangular room sized to the number of
artworks, paintings hung along the walls in `wall_position` order at a consistent eye height with
even spacing, simple wall/floor collision, a click target per painting, texture loading that
degrades gracefully on slow networks and low-end phones.

## Left to the builder

Module layout in the backend (follow the existing structure), exact room dimensions and lighting,
image texture sizing and loading strategy, touch-control design, panel styling, and tests.

## Sequencing

1. Backend: migration, service, endpoints, admin, contract and docs. (This repo.)
2. File the frontend issue with the section above, labelled `needs-frontend` and `contract-change`.
3. The frontend builds the registry, vendor wrapper (preserving today's embed), then the 3D viewer.
