from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_access_token
from app.db.session import get_db
from app.models.user import User

# auto_error=False so a missing header raises our own 401 body instead of FastAPI's
_bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Verifies the access-token JWT and re-reads the user row from the DB.

    Per PRD rule 26, authorisation is never taken from the token's claims alone -
    the access token here only carries `sub`, so every request re-checks
    is_admin/artist_status fresh instead of trusting anything cached in the JWT.
    """
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None:
        raise unauthorized

    user_id = decode_access_token(credentials.credentials)
    if user_id is None:
        raise unauthorized

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalars().first()
    if user is None:
        raise unauthorized

    return user


async def get_current_admin_user(
    current_user: User = Depends(get_current_user),
) -> User:
    if not current_user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return current_user
