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
