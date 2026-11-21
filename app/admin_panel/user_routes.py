import math
import uuid
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin_panel.auth import get_session_admin_user
from app.admin_panel.csrf import verify_csrf
from app.admin_panel.templates import templates
from app.db.session import get_db
from app.models.user import User
from app.services import user_admin as users_service

router = APIRouter()

ROLES = [("all", "All users"), ("admins", "Admins"), ("artists", "Approved artists")]

# action -> (button/title label, what it means)
ACTION_COPY = {
    users_service.ACTION_DEACTIVATE: (
        "Deactivate",
        "They will be signed out everywhere and will not be able to log in until reactivated.",
    ),
    users_service.ACTION_REACTIVATE: ("Reactivate", "They will be able to log in again."),
    users_service.ACTION_PROMOTE: ("Make admin", "They will get full access to the admin panel."),
    users_service.ACTION_DEMOTE: ("Remove admin", "They will lose access to the admin panel."),
}
NOTICES = {
    users_service.ACTION_DEACTIVATE: "Account deactivated.",
    users_service.ACTION_REACTIVATE: "Account reactivated.",
    users_service.ACTION_PROMOTE: "User is now an admin.",
    users_service.ACTION_DEMOTE: "Admin access removed.",
}


def _detail_url(user_id: uuid.UUID, *, error: str | None = None, notice: str | None = None) -> str:
    url = f"/admin/users/{user_id}"
    if error:
        url += f"?error={quote(error)}"
    elif notice:
        url += f"?notice={quote(notice)}"
    return url


@router.get("/users")
async def users_list(
    request: Request,
    q: str = "",
    role: str = "all",
    page: int = 1,
    notice: str | None = None,
    error: str | None = None,
    admin: User = Depends(get_session_admin_user),
    db: AsyncSession = Depends(get_db),
):
    if role not in users_service.ROLE_FILTERS:
        role = "all"
    page = max(page, 1)
    users, total = await users_service.list_users(db, q=q, role=role, page=page)
    pages = max(math.ceil(total / users_service.PAGE_SIZE), 1)

    def page_url(n: int) -> str:
        return "/admin/users?" + urlencode({"q": q, "role": role, "page": n})

    return templates.TemplateResponse(
        request,
        "users_list.html",
        {
            "users": users, "total": total, "q": q, "role": role, "roles": ROLES, "page": page,
            "pages": pages, "page_url": page_url, "notice": notice, "error": error,
        },
    )


@router.get("/users/{user_id}")
async def user_detail(
    request: Request,
    user_id: uuid.UUID,
    notice: str | None = None,
    error: str | None = None,
    admin: User = Depends(get_session_admin_user),
    db: AsyncSession = Depends(get_db),
):
    detail = await users_service.get_user_detail(db, user_id)
    if detail is None:
        return RedirectResponse(url="/admin/users?error=" + quote("User not found."), status_code=303)
    return templates.TemplateResponse(
        request,
        "user_detail.html",
        {
            "user": detail.user, "detail": detail, "is_self": detail.user.id == admin.id,
            "notice": notice, "error": error,
        },
    )


@router.get("/users/{user_id}/confirm/{action}")
async def user_action_confirm(
    request: Request,
    user_id: uuid.UUID,
    action: str,
    admin: User = Depends(get_session_admin_user),
    db: AsyncSession = Depends(get_db),
):
    user = await users_service.get_user(db, user_id)
    if user is None or action not in ACTION_COPY:
        return RedirectResponse(url="/admin/users?error=" + quote("User not found."), status_code=303)
    label, consequence = ACTION_COPY[action]
    return templates.TemplateResponse(
        request,
        "user_confirm.html",
        {"user": user, "action": action, "label": label, "consequence": consequence},
    )


@router.post("/users/{user_id}/{action}", dependencies=[Depends(verify_csrf)])
async def user_action(
    user_id: uuid.UUID,
    action: str,
    admin: User = Depends(get_session_admin_user),
    db: AsyncSession = Depends(get_db),
):
    if action not in ACTION_COPY:
        return RedirectResponse(url="/admin/users?error=" + quote("Unknown action."), status_code=303)
    try:
        await users_service.apply_action(db, admin, user_id, action)
    except users_service.UserAdminError as exc:
        await db.rollback()
        return RedirectResponse(url=_detail_url(user_id, error=str(exc)), status_code=303)
    return RedirectResponse(url=_detail_url(user_id, notice=NOTICES[action]), status_code=303)
