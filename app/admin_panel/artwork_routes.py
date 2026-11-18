import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin_panel.auth import get_session_admin_user
from app.admin_panel.csrf import verify_csrf
from app.admin_panel.templates import templates
from app.db.session import get_db
from app.models.artwork import ArtworkStatus, ListingType
from app.models.user import User
from app.services import artwork_admin
from app.services import artwork_images as images_service
from app.services import artworks as artworks_service
from app.services import image_processing
from app.services.storage import (
    ImageStorage,
    StorageError,
    StorageNotConfiguredError,
    get_image_storage,
)

router = APIRouter()

FORM_FIELDS = (
    "artist_id", "title", "year", "medium", "dimensions", "width_cm", "height_cm",
    "category", "listing_type", "price", "description",
)
LISTING_TYPES = [t.value for t in ListingType]

# Errors from the image service that are shown to the admin as-is.
IMAGE_USER_ERRORS = (
    images_service.ArtworkNotFoundError,
    images_service.ImageNotFoundError,
    images_service.ImageLimitReachedError,
    images_service.LastImageError,
    images_service.InvalidOrderError,
    image_processing.InvalidImageError,
    image_processing.ImageTooLargeError,
    StorageError,
)
STORAGE_NOT_CONFIGURED = "Image storage is not configured on this server."


def _redirect(path: str, *, error: str | None = None, notice: str | None = None) -> RedirectResponse:
    params = []
    if error:
        params.append(f"error={quote(error)}")
    if notice:
        params.append(f"notice={quote(notice)}")
    url = path + ("?" + "&".join(params) if params else "")
    return RedirectResponse(url=url, status_code=303)


def _edit_url(artwork_id: uuid.UUID) -> str:
    return f"/admin/artworks/{artwork_id}/edit"


def _values_from_artwork(artwork) -> dict:
    def s(v):
        return "" if v is None else str(v)

    return {
        "artist_id": str(artwork.artist_id),
        "title": s(artwork.title),
        "year": s(artwork.year),
        "medium": s(artwork.medium),
        "dimensions": s(artwork.dimensions),
        "width_cm": s(artwork.width_cm),
        "height_cm": s(artwork.height_cm),
        "category": s(artwork.category),
        "listing_type": artwork.listing_type.value,
        "price": s(artwork.price),
        "description": s(artwork.description),
    }


async def _render_form(
    request: Request,
    db: AsyncSession,
    storage: ImageStorage,
    *,
    artwork=None,
    values: dict,
    errors: dict | None = None,
    error: str | None = None,
    notice: str | None = None,
    status_code: int = 200,
):
    images = await images_service.list_images(db, artwork.id) if artwork else []
    listing_types = LISTING_TYPES
    if artwork is None or artwork.listing_type != ListingType.auction:
        # Auction listings need an auction row (PRD rule 8); auctions are not built yet.
        listing_types = [t for t in LISTING_TYPES if t != ListingType.auction.value]
    return templates.TemplateResponse(
        request,
        "artwork_form.html",
        {
            "artwork": artwork,
            "values": values,
            "errors": errors or {},
            "error": error,
            "notice": notice,
            "artists": await artwork_admin.list_artist_choices(db),
            "images": images,
            "statuses": [s.value for s in ArtworkStatus],
            "listing_types": listing_types,
            "storage_configured": storage.configured,
        },
        status_code=status_code,
    )


async def _form_values(request: Request) -> dict:
    form = await request.form()
    return {name: str(form.get(name) or "") for name in FORM_FIELDS}


@router.get("/artworks")
async def artworks_list(
    request: Request,
    status: str | None = None,
    error: str | None = None,
    notice: str | None = None,
    admin: User = Depends(get_session_admin_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        rows = await artworks_service.list_artworks(db, status=status)
    except Exception:
        await db.rollback()
        rows = await artworks_service.list_artworks(db, status=None)
    return templates.TemplateResponse(
        request,
        "artworks_list.html",
        {
            "artworks": rows,
            "current_status": status,
            "statuses": [s.value for s in ArtworkStatus],
            "error": error,
            "notice": notice,
        },
    )


@router.get("/artworks/new")
async def artwork_new_form(
    request: Request,
    admin: User = Depends(get_session_admin_user),
    db: AsyncSession = Depends(get_db),
    storage: ImageStorage = Depends(get_image_storage),
):
    values = {name: "" for name in FORM_FIELDS}
    values["listing_type"] = ListingType.display.value
    return await _render_form(request, db, storage, values=values)


@router.post("/artworks/new", dependencies=[Depends(verify_csrf)])
async def artwork_create(
    request: Request,
    admin: User = Depends(get_session_admin_user),
    db: AsyncSession = Depends(get_db),
    storage: ImageStorage = Depends(get_image_storage),
):
    values = await _form_values(request)
    try:
        artwork = await artwork_admin.create_artwork(db, values)
    except artwork_admin.ArtworkValidationError as exc:
        return await _render_form(
            request, db, storage, values=values, errors=exc.errors, status_code=422
        )
    return _redirect(
        _edit_url(artwork.id), notice="Draft created. Add images below, then publish it."
    )


@router.get("/artworks/{artwork_id}/edit")
async def artwork_edit_form(
    request: Request,
    artwork_id: uuid.UUID,
    error: str | None = None,
    notice: str | None = None,
    admin: User = Depends(get_session_admin_user),
    db: AsyncSession = Depends(get_db),
    storage: ImageStorage = Depends(get_image_storage),
):
    artwork = await artworks_service.get_artwork_by_id(db, artwork_id)
    if artwork is None:
        return _redirect("/admin/artworks", error="Artwork not found.")
    return await _render_form(
        request, db, storage, artwork=artwork, values=_values_from_artwork(artwork),
        error=error, notice=notice,
    )


@router.post("/artworks/{artwork_id}/edit", dependencies=[Depends(verify_csrf)])
async def artwork_update(
    request: Request,
    artwork_id: uuid.UUID,
    admin: User = Depends(get_session_admin_user),
    db: AsyncSession = Depends(get_db),
    storage: ImageStorage = Depends(get_image_storage),
):
    artwork = await artworks_service.get_artwork_by_id(db, artwork_id)
    if artwork is None:
        return _redirect("/admin/artworks", error="Artwork not found.")
    values = await _form_values(request)
    try:
        await artwork_admin.update_artwork(db, artwork, values)
    except artwork_admin.ArtworkValidationError as exc:  # validation runs before any change
        return await _render_form(
            request, db, storage, artwork=artwork, values=values, errors=exc.errors,
            status_code=422,
        )
    return _redirect(_edit_url(artwork_id), notice="Changes saved.")


@router.get("/artworks/{artwork_id}/delete")
async def artwork_delete_confirm(
    request: Request,
    artwork_id: uuid.UUID,
    error: str | None = None,
    admin: User = Depends(get_session_admin_user),
    db: AsyncSession = Depends(get_db),
):
    artwork = await artworks_service.get_artwork_by_id(db, artwork_id)
    if artwork is None:
        return _redirect("/admin/artworks", error="Artwork not found.")
    artist_name = dict(await artwork_admin.list_artist_choices(db)).get(artwork.artist_id, "")
    return templates.TemplateResponse(
        request,
        "artwork_delete.html",
        {"artwork": artwork, "artist_name": artist_name, "error": error},
    )


@router.post("/artworks/{artwork_id}/delete", dependencies=[Depends(verify_csrf)])
async def artwork_delete(
    artwork_id: uuid.UUID,
    admin: User = Depends(get_session_admin_user),
    db: AsyncSession = Depends(get_db),
):
    artwork = await artworks_service.get_artwork_by_id(db, artwork_id)
    if artwork is None:
        return _redirect("/admin/artworks", error="Artwork not found.")
    try:
        await artwork_admin.delete_artwork(db, artwork)
    except artwork_admin.ArtworkNotDeletableError as exc:
        return _redirect(f"/admin/artworks/{artwork_id}/delete", error=str(exc))
    return _redirect("/admin/artworks", notice=f"Deleted {artwork.title}.")


@router.post("/artworks/{artwork_id}/status", dependencies=[Depends(verify_csrf)])
async def update_artwork_status_form(
    artwork_id: uuid.UUID,
    new_status: str = Form(...),
    next: str | None = None,
    admin: User = Depends(get_session_admin_user),
    db: AsyncSession = Depends(get_db),
):
    back = _edit_url(artwork_id) if next == "edit" else "/admin/artworks"
    artwork = await artworks_service.get_artwork_by_id(db, artwork_id)
    if artwork is not None:
        try:
            await artworks_service.set_artwork_status(db, artwork, new_status)
        except (
            artworks_service.InvalidArtworkStatusError,
            artworks_service.ArtworkNotPublishableError,
        ) as exc:
            return _redirect(back, error=str(exc))
        if next == "edit":
            return _redirect(back, notice="Status updated.")
    return RedirectResponse(url=back, status_code=303)


@router.post("/artworks/{artwork_id}/images", dependencies=[Depends(verify_csrf)])
async def artwork_images_upload(
    artwork_id: uuid.UUID,
    files: list[UploadFile] = File(default=[]),
    admin: User = Depends(get_session_admin_user),
    db: AsyncSession = Depends(get_db),
    storage: ImageStorage = Depends(get_image_storage),
):
    back = _edit_url(artwork_id)
    if not storage.configured:
        return _redirect(back, error=STORAGE_NOT_CONFIGURED)
    files = [f for f in files if f.filename]
    if not files:
        return _redirect(back, error="Choose at least one image to upload.")
    uploaded, problems = 0, []
    for upload in files:
        # Only the bytes matter; the filename is shown in error messages (escaped by Jinja)
        # but never used for storage.
        data = await upload.read(image_processing.MAX_UPLOAD_BYTES + 1)
        try:
            await images_service.add_image(db, storage, artwork_id, data)
            uploaded += 1
        except (*IMAGE_USER_ERRORS, StorageNotConfiguredError) as exc:
            problems.append(f"{upload.filename}: {exc}")
            if isinstance(exc, (images_service.ArtworkNotFoundError, images_service.ImageLimitReachedError)):
                break
    notice = f"Uploaded {uploaded} image{'s' if uploaded != 1 else ''}." if uploaded else None
    return _redirect(back, error=" ".join(problems) or None, notice=notice)


async def _image_action(artwork_id: uuid.UUID, action, success: str) -> RedirectResponse:
    try:
        await action()
    except (*IMAGE_USER_ERRORS, StorageNotConfiguredError) as exc:
        if isinstance(exc, StorageNotConfiguredError):
            return _redirect(_edit_url(artwork_id), error=STORAGE_NOT_CONFIGURED)
        return _redirect(_edit_url(artwork_id), error=str(exc))
    return _redirect(_edit_url(artwork_id), notice=success)


@router.post("/artworks/{artwork_id}/images/{image_id}/delete", dependencies=[Depends(verify_csrf)])
async def artwork_image_delete(
    artwork_id: uuid.UUID,
    image_id: uuid.UUID,
    admin: User = Depends(get_session_admin_user),
    db: AsyncSession = Depends(get_db),
    storage: ImageStorage = Depends(get_image_storage),
):
    return await _image_action(
        artwork_id,
        lambda: images_service.delete_image(db, storage, artwork_id, image_id),
        "Image deleted.",
    )


@router.post("/artworks/{artwork_id}/images/{image_id}/primary", dependencies=[Depends(verify_csrf)])
async def artwork_image_primary(
    artwork_id: uuid.UUID,
    image_id: uuid.UUID,
    admin: User = Depends(get_session_admin_user),
    db: AsyncSession = Depends(get_db),
):
    return await _image_action(
        artwork_id, lambda: images_service.set_primary(db, artwork_id, image_id), "Primary image set."
    )


@router.post("/artworks/{artwork_id}/images/{image_id}/move", dependencies=[Depends(verify_csrf)])
async def artwork_image_move(
    artwork_id: uuid.UUID,
    image_id: uuid.UUID,
    direction: str = Form(...),
    admin: User = Depends(get_session_admin_user),
    db: AsyncSession = Depends(get_db),
):
    step = -1 if direction == "up" else 1
    return await _image_action(
        artwork_id, lambda: images_service.move_image(db, artwork_id, image_id, step), "Image order updated."
    )
