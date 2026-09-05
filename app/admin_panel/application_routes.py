import uuid
from urllib.parse import quote
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
    error: str | None = None,
    admin: User = Depends(get_session_admin_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        status_filter = ApplicationStatus(status) if status else None
    except ValueError:
        status_filter = None
    rows = await applications_service.list_applications(db, status=status_filter)
    return templates.TemplateResponse(
        request,
        "applications_list.html",
        {
            "rows": rows,
            "current_status": status,
            "statuses": [s.value for s in ApplicationStatus],
            "error": error,
        },
    )


@router.get("/applications/{application_id}")
async def application_detail(
    request: Request,
    application_id: uuid.UUID,
    error: str | None = None,
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
        {
            "application": application,
            "works": works,
            "applicant_email": applicant_email,
            "error": error,
        },
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
        except applications_service.ApplicationNotEditableError as exc:
            return RedirectResponse(
                url=f"/admin/applications/{application_id}?error={quote(str(exc))}",
                status_code=303,
            )
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
        except applications_service.ApplicationNotEditableError as exc:
            return RedirectResponse(
                url=f"/admin/applications/{application_id}?error={quote(str(exc))}",
                status_code=303,
            )
    return RedirectResponse(url="/admin/applications", status_code=303)
