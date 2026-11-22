import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin_panel.auth import get_session_admin_user
from app.admin_panel.csrf import verify_csrf
from app.admin_panel.templates import templates
from app.db.session import get_db
from app.models.exhibition import Exhibition, ViewerKind
from app.models.user import User
from app.services import artworks as artworks_service
from app.services import exhibition_admin as svc

router = APIRouter()

FORM_FIELDS = (
    "title", "viewer_kind", "vr_tour_url", "description", "cover_image_url",
    "starts_on", "ends_on", "sort_order",
)
VIEWER_KINDS = [
    (ViewerKind.vendor_embed.value, "External viewer (embed a link, e.g. Artsteps)"),
    (ViewerKind.first_party.value, "Our own viewer (choose artworks after saving)"),
]
NOT_FOUND = "Gallery not found."
BAD_POSITION = "Wall position must be a whole number from 0 to 32767."


def _redirect(path: str, *, error: str | None = None, notice: str | None = None) -> RedirectResponse:
    params = []
    if error:
        params.append(f"error={quote(error)}")
    if notice:
        params.append(f"notice={quote(notice)}")
    url = path + ("?" + "&".join(params) if params else "")
    return RedirectResponse(url=url, status_code=303)


def _edit_url(exhibition_id: uuid.UUID) -> str:
    return f"/admin/exhibitions/{exhibition_id}/edit"


def _values_from_exhibition(ex: Exhibition) -> dict:
    def s(v):
        return "" if v is None else str(v)

    return {name: s(getattr(ex, name)) for name in FORM_FIELDS}


async def _form_values(request: Request) -> dict:
    form = await request.form()
    return {name: str(form.get(name) or "") for name in FORM_FIELDS}


async def _render_form(
    request: Request,
    db: AsyncSession,
    *,
    exhibition: Exhibition | None = None,
    values: dict,
    errors: dict | None = None,
    error: str | None = None,
    notice: str | None = None,
    status_code: int = 200,
):
    assigned: dict = {}
    choices: list = []
    if exhibition is not None and exhibition.viewer_kind == ViewerKind.first_party.value:
        assigned = await svc.list_assignments(db, exhibition.id)
        choices = await artworks_service.list_artworks(db)
    return templates.TemplateResponse(
        request,
        "exhibition_form.html",
        {
            "exhibition": exhibition,
            "values": values,
            "errors": errors or {},
            "error": error,
            "notice": notice,
            "viewer_kinds": VIEWER_KINDS,
            "artwork_choices": choices,
            "assigned": assigned,
        },
        status_code=status_code,
    )


@router.get("/exhibitions")
async def exhibitions_list(
    request: Request,
    error: str | None = None,
    notice: str | None = None,
    admin: User = Depends(get_session_admin_user),
    db: AsyncSession = Depends(get_db),
):
    return templates.TemplateResponse(
        request,
        "exhibitions_list.html",
        {"exhibitions": await svc.list_exhibitions(db), "error": error, "notice": notice},
    )


@router.get("/exhibitions/new")
async def exhibition_new_form(
    request: Request,
    admin: User = Depends(get_session_admin_user),
    db: AsyncSession = Depends(get_db),
):
    # viewer_kind is deliberately left unselected: the admin must choose it.
    return await _render_form(request, db, values={name: "" for name in FORM_FIELDS})


@router.post("/exhibitions/new", dependencies=[Depends(verify_csrf)])
async def exhibition_create(
    request: Request,
    admin: User = Depends(get_session_admin_user),
    db: AsyncSession = Depends(get_db),
):
    values = await _form_values(request)
    try:
        exhibition = await svc.create_exhibition(db, svc.clean_exhibition_form(values))
    except svc.ExhibitionValidationError as exc:
        return await _render_form(request, db, values=values, errors=exc.errors, status_code=422)
    return _redirect(
        _edit_url(exhibition.id), notice="Gallery created as a draft. Publish it when it is ready."
    )


@router.get("/exhibitions/{exhibition_id}/edit")
async def exhibition_edit_form(
    request: Request,
    exhibition_id: uuid.UUID,
    error: str | None = None,
    notice: str | None = None,
    admin: User = Depends(get_session_admin_user),
    db: AsyncSession = Depends(get_db),
):
    exhibition = await svc.get_exhibition(db, exhibition_id)
    if exhibition is None:
        return _redirect("/admin/exhibitions", error=NOT_FOUND)
    return await _render_form(
        request, db, exhibition=exhibition, values=_values_from_exhibition(exhibition),
        error=error, notice=notice,
    )


@router.post("/exhibitions/{exhibition_id}/edit", dependencies=[Depends(verify_csrf)])
async def exhibition_update(
    request: Request,
    exhibition_id: uuid.UUID,
    admin: User = Depends(get_session_admin_user),
    db: AsyncSession = Depends(get_db),
):
    exhibition = await svc.get_exhibition(db, exhibition_id)
    if exhibition is None:
        return _redirect("/admin/exhibitions", error=NOT_FOUND)
    values = await _form_values(request)
    try:
        await svc.update_exhibition(db, exhibition_id, svc.clean_exhibition_form(values, exhibition))
    except svc.ExhibitionValidationError as exc:
        await db.rollback()
        exhibition = await svc.get_exhibition(db, exhibition_id)
        return await _render_form(
            request, db, exhibition=exhibition, values=values, errors=exc.errors, status_code=422
        )
    return _redirect(_edit_url(exhibition_id), notice="Changes saved.")


@router.post("/exhibitions/{exhibition_id}/artworks", dependencies=[Depends(verify_csrf)])
async def exhibition_set_artworks(
    request: Request,
    exhibition_id: uuid.UUID,
    admin: User = Depends(get_session_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Form fields: `artwork` (repeated, one per ticked artwork) and `position_<artwork id>`."""
    form = await request.form()
    positions: dict[uuid.UUID, int | None] = {}
    for raw_id in form.getlist("artwork"):
        try:
            art_id = uuid.UUID(str(raw_id))
        except ValueError:
            return _redirect(_edit_url(exhibition_id), error="Choose artworks from the list.")
        pos_raw = str(form.get(f"position_{art_id}") or "").strip()
        if not pos_raw:
            positions[art_id] = None
        elif pos_raw.isdecimal() and int(pos_raw) <= svc.MAX_SORT_ORDER:
            positions[art_id] = int(pos_raw)
        else:
            return _redirect(_edit_url(exhibition_id), error=BAD_POSITION)
    try:
        await svc.set_artworks(db, exhibition_id, positions)
    except svc.ExhibitionNotFoundError:
        return _redirect("/admin/exhibitions", error=NOT_FOUND)
    except svc.ExhibitionValidationError as exc:
        return _redirect(_edit_url(exhibition_id), error=" ".join(exc.errors.values()))
    return _redirect(_edit_url(exhibition_id), notice="Artworks updated.")


async def _publish(exhibition_id: uuid.UUID, db: AsyncSession, published: bool, next: str | None):
    target = _edit_url(exhibition_id) if next == "edit" else "/admin/exhibitions"
    try:
        await svc.set_published(db, exhibition_id, published)
    except svc.ExhibitionNotFoundError:
        return _redirect("/admin/exhibitions", error=NOT_FOUND)
    except svc.ExhibitionValidationError as exc:
        return _redirect(target, error=" ".join(exc.errors.values()))
    return _redirect(target, notice="Gallery published." if published else "Gallery unpublished.")


@router.post("/exhibitions/{exhibition_id}/publish", dependencies=[Depends(verify_csrf)])
async def exhibition_publish(
    exhibition_id: uuid.UUID,
    next: str | None = None,
    admin: User = Depends(get_session_admin_user),
    db: AsyncSession = Depends(get_db),
):
    return await _publish(exhibition_id, db, True, next)


@router.post("/exhibitions/{exhibition_id}/unpublish", dependencies=[Depends(verify_csrf)])
async def exhibition_unpublish(
    exhibition_id: uuid.UUID,
    next: str | None = None,
    admin: User = Depends(get_session_admin_user),
    db: AsyncSession = Depends(get_db),
):
    return await _publish(exhibition_id, db, False, next)


@router.get("/exhibitions/{exhibition_id}/delete")
async def exhibition_delete_confirm(
    request: Request,
    exhibition_id: uuid.UUID,
    admin: User = Depends(get_session_admin_user),
    db: AsyncSession = Depends(get_db),
):
    exhibition = await svc.get_exhibition(db, exhibition_id)
    if exhibition is None:
        return _redirect("/admin/exhibitions", error=NOT_FOUND)
    return templates.TemplateResponse(request, "exhibition_delete.html", {"exhibition": exhibition})


@router.post("/exhibitions/{exhibition_id}/delete", dependencies=[Depends(verify_csrf)])
async def exhibition_delete(
    exhibition_id: uuid.UUID,
    admin: User = Depends(get_session_admin_user),
    db: AsyncSession = Depends(get_db),
):
    exhibition = await svc.get_exhibition(db, exhibition_id)
    if exhibition is None:
        return _redirect("/admin/exhibitions", error=NOT_FOUND)
    title = exhibition.title
    await svc.delete_exhibition(db, exhibition_id)
    return _redirect("/admin/exhibitions", notice=f"Deleted {title}.")
