import uuid
from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_admin_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.artwork import ArtworkImageOut, ImageOrderUpdate
from app.services import artwork_images as images_service
from app.services import image_processing
from app.services.artworks import get_artwork_by_id
from app.services.storage import (
    ImageStorage,
    StorageError,
    StorageNotConfiguredError,
    get_image_storage,
)

router = APIRouter()

STORAGE_NOT_CONFIGURED = "Image storage is not configured."


def to_http_error(exc: Exception) -> HTTPException:
    """Maps service-layer image errors to HTTP errors. Shared by every route in this module."""
    if isinstance(exc, (images_service.ArtworkNotFoundError, images_service.ImageNotFoundError)):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, image_processing.ImageTooLargeError):
        return HTTPException(status_code=413, detail=str(exc))
    if isinstance(exc, (image_processing.InvalidImageError, images_service.InvalidOrderError)):
        return HTTPException(status_code=422, detail=str(exc))
    if isinstance(exc, (images_service.ImageLimitReachedError, images_service.LastImageError)):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, StorageNotConfiguredError):
        return HTTPException(status_code=503, detail=STORAGE_NOT_CONFIGURED)
    if isinstance(exc, StorageError):
        return HTTPException(status_code=502, detail=str(exc))
    raise exc


_HANDLED = (
    images_service.ArtworkNotFoundError,
    images_service.ImageNotFoundError,
    images_service.ImageLimitReachedError,
    images_service.LastImageError,
    images_service.InvalidOrderError,
    image_processing.InvalidImageError,
    image_processing.ImageTooLargeError,
    StorageNotConfiguredError,
    StorageError,
)


@router.get("/admin/artworks/{artwork_id}/images", response_model=list[ArtworkImageOut])
async def list_artwork_images_route(
    artwork_id: uuid.UUID,
    current_admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    if await get_artwork_by_id(db, artwork_id) is None:
        raise HTTPException(status_code=404, detail=images_service.ARTWORK_NOT_FOUND)
    return await images_service.list_images(db, artwork_id)


@router.post(
    "/admin/artworks/{artwork_id}/images", response_model=ArtworkImageOut, status_code=201
)
async def upload_artwork_image_route(
    artwork_id: uuid.UUID,
    file: UploadFile = File(...),
    alt_text: str | None = Form(default=None),
    current_admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
    storage: ImageStorage = Depends(get_image_storage),
):
    if not storage.configured:
        raise HTTPException(status_code=503, detail=STORAGE_NOT_CONFIGURED)
    # Read at most one byte past the limit so an oversized upload is rejected without buffering
    # all of it. The filename and declared content type are deliberately ignored.
    data = await file.read(image_processing.MAX_UPLOAD_BYTES + 1)
    try:
        return await images_service.add_image(db, storage, artwork_id, data, alt_text)
    except _HANDLED as exc:
        raise to_http_error(exc)


@router.put("/admin/artworks/{artwork_id}/images/order", response_model=list[ArtworkImageOut])
async def reorder_artwork_images_route(
    artwork_id: uuid.UUID,
    payload: ImageOrderUpdate,
    current_admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await images_service.reorder_images(db, artwork_id, payload.image_ids)
    except _HANDLED as exc:
        raise to_http_error(exc)


@router.post(
    "/admin/artworks/{artwork_id}/images/{image_id}/primary",
    response_model=list[ArtworkImageOut],
)
async def set_primary_artwork_image_route(
    artwork_id: uuid.UUID,
    image_id: uuid.UUID,
    current_admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await images_service.set_primary(db, artwork_id, image_id)
    except _HANDLED as exc:
        raise to_http_error(exc)


@router.delete("/admin/artworks/{artwork_id}/images/{image_id}", status_code=204)
async def delete_artwork_image_route(
    artwork_id: uuid.UUID,
    image_id: uuid.UUID,
    current_admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
    storage: ImageStorage = Depends(get_image_storage),
):
    try:
        await images_service.delete_image(db, storage, artwork_id, image_id)
    except _HANDLED as exc:
        raise to_http_error(exc)
    return Response(status_code=204)
