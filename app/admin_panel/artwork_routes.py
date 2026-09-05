import uuid
from urllib.parse import quote
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
    error: str | None = None,
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
        },
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
        except (
            artworks_service.InvalidArtworkStatusError,
            artworks_service.ArtworkNotPublishableError,
        ) as exc:
            return RedirectResponse(
                url=f"/admin/artworks?error={quote(str(exc))}", status_code=303
            )
    return RedirectResponse(url="/admin/artworks", status_code=303)
