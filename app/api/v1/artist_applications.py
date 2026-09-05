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
