# Issue body: VR galleries, vendor and first-party side by side

Repo: mekriha-tech/picasso-frontend
Labels: `needs-frontend`, `contract-change`
Not yet filed: the product owner must approve before `gh issue create` is run.

## Summary

The backend now exposes VR galleries (API name: exhibitions) of two kinds, mixed freely in one
list: `vendor_embed` (today's Artsteps iframe) and `first_party` (a 3D room Picasso renders itself
from artwork data, where visitors can click a painting and reach its buy page).

Contract: `docs/API_REFERENCE.md` ("Exhibitions") and `docs/openapi.json` in picasso-backend.
Design: `docs/superpowers/specs/2026-11-22-vr-galleries-design.md` (backend repo).

## API (both endpoints are public; the token matters on the detail call)

- `GET /exhibitions` needs no auth.
- `GET /exhibitions/{slug}` is public, but when the user is logged in you must send their access
  token (`Authorization: Bearer ...`). Without it the server treats them as logged out and returns
  the teaser room. An expired or invalid token returns `401 {"detail":"Not authenticated"}` (it
  does not fall back to anonymous), so refresh the token and retry.
- `teaser` is `true` only for logged-out callers of a `first_party` gallery. Vendor galleries
  always return `teaser: false`, so the generic page must not branch on it for vendor galleries.


`GET /api/v1/exhibitions`, published galleries ordered by `sort_order`, one button each
("Visit VR gallery 1", "Visit VR gallery 2", ...):

```json
{ "items": [
  { "id": "uuid", "title": "Monsoon Light", "slug": "monsoon-light",
    "cover_image_url": "https://...", "viewer_kind": "vendor_embed" }
] }
```

`GET /api/v1/exhibitions/{slug}`, `viewer_kind: "vendor_embed"` (`artworks` is absent):

```json
{ "id": "uuid", "title": "Monsoon Light", "slug": "monsoon-light",
  "cover_image_url": "https://...", "viewer_kind": "vendor_embed",
  "description": "...", "starts_on": "2026-12-01", "ends_on": null,
  "vr_tour_url": "https://www.artsteps.com/embed/...", "teaser": false }
```

`GET /api/v1/exhibitions/{slug}`, `viewer_kind: "first_party"` (`vr_tour_url` is absent):

```json
{ "id": "uuid", "title": "The Open Room", "slug": "the-open-room",
  "cover_image_url": null, "viewer_kind": "first_party",
  "description": null, "starts_on": null, "ends_on": null,
  "artworks": [
    { "id": "uuid", "slug": "monsoon-over-marina", "title": "Monsoon Over Marina",
      "artist": { "slug": "meera-iyer", "display_name": "Meera Iyer" },
      "primary_image_url": "https://...", "medium": "Oil on canvas",
      "dimensions": "36 x 24 inches", "year": 2023, "listing_type": "sale",
      "status": "published", "badge": "For Sale", "price": "185000.00", "sold": false,
      "sold_price": null, "auction": null, "in_wishlist": false,
      "wall_position": 1, "width_cm": "91.44", "height_cm": "60.96",
      "texture_url": "https://...", "image_width_px": 1000, "image_height_px": 667 }
  ],
  "teaser": false }
```

Notes:
- `404 {"detail": "Exhibition not found"}` for an unknown or unpublished slug.
- Artworks are ordered by `wall_position` ascending, `null` last. Hang them in that order.
- There is **no buy-link field**. Build the buy page URL from the artwork `slug` with your own routes.
- Money is a string (`price`); format it on the frontend (INR).
- `texture_url` is the image to load as the painting texture; `image_width_px` / `image_height_px`
  give the aspect ratio; `width_cm` / `height_cm` give physical size when known (all may be `null`).
- Logged-out teaser: without a token, a first-party gallery's `artworks` is limited to teaser works
  and `"teaser": true`. Show a "Log in to see the full gallery" prompt when `teaser` is true (first-party only; vendor galleries always return false).

## Frontend design (organization is a requirement)

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
- Adding a gallery = admin data only. Adding a viewer kind = a new folder plus one registry entry.
- **Fallback**: an unknown `viewer_kind` renders a safe fallback message, never a crash.
- **Vendor viewer**: wraps `vr_tour_url` in an iframe, preserving today's behaviour.
- **First-party viewer**: react-three-fiber + drei, dynamically imported so the 3D code is only
  downloaded when a first-party gallery is opened.
  - Controls: pointer-lock mouse-look plus WASD on desktop; touch controls (e.g. joystick) on mobile.
  - Room: a rectangular room sized to the number of artworks, paintings hung along the walls in
    `wall_position` order at a consistent eye height with even spacing, simple wall/floor
    collision, one click target per painting, texture loading that degrades gracefully on slow
    networks and low-end phones.
  - Clicking a painting opens a panel with title, artist, price, sold/status, and a Buy link
    built from the artwork `slug`.

## Acceptance

- List page shows one button per published gallery, in API order, for both kinds.
- Vendor galleries look and behave as today.
- First-party galleries are walkable on desktop and mobile; click-a-painting panel works.
- No gallery-specific or kind-specific code outside `registry.ts` and `viewers/`.
- Unknown `viewer_kind` shows the fallback.

## Sequencing

1. Registry plus vendor wrapper (preserve the current embed).
2. First-party 3D viewer.
