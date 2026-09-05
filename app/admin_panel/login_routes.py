from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin_panel.auth import authenticate_admin, get_session_admin_user
from app.admin_panel.templates import templates
from app.db.session import get_db
from app.models.user import User

router = APIRouter()


@router.get("")
async def admin_root(admin: User = Depends(get_session_admin_user)):
    return RedirectResponse(url="/admin/applications", status_code=303)


@router.get("/login")
async def login_form(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login")
async def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    user = await authenticate_admin(db, email, password)
    if user is None:
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "Invalid email, password, or this account isn't an admin."},
            status_code=401,
        )
    request.session["admin_user_id"] = str(user.id)
    return RedirectResponse(url="/admin/applications", status_code=303)


@router.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/admin/login", status_code=303)
