from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import Depends, Request

from app.core.security import verify_password
from app.db.session import get_db
from app.models.user import User


class AdminAuthRequired(Exception):
    """Raised by get_session_admin_user; app.main registers a handler that redirects to /admin/login."""


async def get_session_admin_user(request: Request, db: AsyncSession = Depends(get_db)) -> User:
    user_id = request.session.get("admin_user_id")
    if not user_id:
        raise AdminAuthRequired()
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalars().first()
    if user is None or not user.is_admin:
        request.session.clear()
        raise AdminAuthRequired()
    return user


async def authenticate_admin(db: AsyncSession, email: str, password: str) -> User | None:
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalars().first()
    if user is None or not user.password_hash or not user.is_admin:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user
