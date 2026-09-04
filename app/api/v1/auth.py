import uuid
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, status, Response, Request, Cookie
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from sqlalchemy.sql import func
from sqlalchemy.exc import IntegrityError

from app.db.session import get_db
from app.models.user import User
from app.models.refresh_token import RefreshToken
from app.schemas.user import UserCreate, RegisterResponse
from app.core.security import (
    get_password_hash,
    create_access_token,
    generate_opaque_token,
    hash_token,
    set_refresh_token_cookie,
    clear_refresh_token_cookie,
    REFRESH_TOKEN_TTL_DAYS,
)

# Concurrent /auth/refresh calls with the same cookie race on the row lock (see
# refresh_token() below); if the rotated-parent was revoked moments ago and already
# has a child in the same family, treat it as that benign race, not a replay.
REUSE_GRACE_PERIOD = timedelta(seconds=10)

router = APIRouter()

@router.post("/register", response_model=RegisterResponse, status_code=status.HTTP_201_CREATED)
async def register_user(
    user_in: UserCreate, 
    request: Request,
    response: Response, 
    db: AsyncSession = Depends(get_db)
):
    # 1. Fast-fail check for existing email
    result = await db.execute(select(User).where(User.email == user_in.email))
    if result.scalars().first():
        raise HTTPException(status_code=400, detail="Email already registered")
    
    # 2. Hash password with Argon2id
    new_user = User(
        email=user_in.email,
        full_name=user_in.full_name,
        password_hash=get_password_hash(user_in.password) 
    )
    
    # 3. Add to DB and handle race conditions. Flush (not commit) so new_user.id
    # is assigned but the row isn't durable yet - the refresh token below is
    # inserted in the SAME transaction, so a failure here can't orphan a user
    # with no way to obtain credentials.
    db.add(new_user)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=400, detail="Email already registered")

    # 4. Generate Access Token (15 min JWT)
    access_token = create_access_token(subject=str(new_user.id))

    # 5. Generate Opaque Refresh Token (7 days, stored hashed in DB)
    raw_refresh_token = generate_opaque_token()
    token_expires_at = datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_TTL_DAYS)

    db_refresh_token = RefreshToken(
        user_id=new_user.id,
        token_hash=hash_token(raw_refresh_token),
        family_id=uuid.uuid4(),
        expires_at=token_expires_at,
        user_agent=request.headers.get("user-agent"),
        ip_address=request.client.host if request.client else None
    )

    db.add(db_refresh_token)
    await db.commit()
    await db.refresh(new_user)

    # 6. Set httpOnly cookie for refresh token using the RAW token
    set_refresh_token_cookie(response, raw_refresh_token)

    return {
        "user": new_user,
        "access_token": access_token
    }


@router.post("/refresh")
async def refresh_token(
    request: Request,
    response: Response,
    refresh_token: str | None = Cookie(default=None),
    db: AsyncSession = Depends(get_db)
):
    if not refresh_token:
        raise HTTPException(status_code=401, detail="No refresh token")

    hashed_token = hash_token(refresh_token)
    
    # 1. Fetch token with row lock to prevent parallel refresh race conditions
    result = await db.execute(
        select(RefreshToken)
        .where(RefreshToken.token_hash == hashed_token)
        .with_for_update()
    )
    db_token = result.scalars().first()

    if not db_token:
        raise HTTPException(status_code=401, detail="Invalid token")

    # 2. Reuse Detection (The Replay Trap)
    if db_token.revoked_at:
        # Two concurrent /refresh calls with the same cookie both block on the
        # `SELECT ... FOR UPDATE` above for this row. Whichever commits first
        # rotates it; the second then sees `revoked_at` already set here. That
        # is a benign race (PRD §3.9 rule 3: "two simultaneous refreshes ...
        # must not both succeed and mutually revoke the session"), not a
        # replay attack - only treat it as reuse once the grace window during
        # which a legitimate concurrent rotation could have happened has
        # passed, or no matching child token exists.
        now = datetime.now(timezone.utc)
        if (
            db_token.revoked_reason == "rotated"
            and now - db_token.revoked_at <= REUSE_GRACE_PERIOD
        ):
            child_result = await db.execute(
                select(RefreshToken).where(RefreshToken.parent_id == db_token.id)
            )
            child_token = child_result.scalars().first()
            if child_token and not child_token.revoked_at:
                # The other request already rotated this token and set the new
                # cookie; just hand this caller a fresh access token for the
                # same session instead of tearing the family down.
                access_token = create_access_token(subject=str(child_token.user_id))
                return {"access_token": access_token}

        await db.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == db_token.family_id)
            .where(RefreshToken.revoked_at.is_(None))
            .values(revoked_at=func.now(), revoked_reason="reuse_detected")
        )
        await db.commit()
        raise HTTPException(status_code=401, detail="session_revoked")

    if db_token.expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=401, detail="Token expired")

    # 3. Rotate Token
    db_token.revoked_at = func.now()
    db_token.revoked_reason = "rotated"

    new_raw_token = generate_opaque_token()
    new_db_token = RefreshToken(
        user_id=db_token.user_id,
        token_hash=hash_token(new_raw_token),
        family_id=db_token.family_id,
        parent_id=db_token.id,
        expires_at=datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_TTL_DAYS),
        user_agent=request.headers.get("user-agent"),
        ip_address=request.client.host if request.client else None
    )

    db.add(new_db_token)
    await db.commit()

    # 4. Issue new tokens
    access_token = create_access_token(subject=str(db_token.user_id))
    set_refresh_token_cookie(response, new_raw_token)

    return {"access_token": access_token}


@router.post("/logout")
async def logout(
    response: Response,
    refresh_token: str | None = Cookie(default=None),
    db: AsyncSession = Depends(get_db)
):
    if refresh_token:
        hashed_token = hash_token(refresh_token)
        await db.execute(
            update(RefreshToken)
            .where(RefreshToken.token_hash == hashed_token)
            .values(revoked_at=func.now(), revoked_reason="logout")
        )
        await db.commit()

    clear_refresh_token_cookie(response)
    return {"detail": "Logged out successfully"}

@router.post("/logout-all")
async def logout_all(
    response: Response,
    refresh_token: str | None = Cookie(default=None),
    db: AsyncSession = Depends(get_db)
):
    if refresh_token:
        hashed_token = hash_token(refresh_token)
        # 1. Find who is making the request
        result = await db.execute(
            select(RefreshToken).where(RefreshToken.token_hash == hashed_token)
        )
        current_token = result.scalars().first()

        if current_token:
            # 2. Revoke EVERY active token for this user
            await db.execute(
                update(RefreshToken)
                .where(RefreshToken.user_id == current_token.user_id)
                .where(RefreshToken.revoked_at.is_(None))
                .values(revoked_at=func.now(), revoked_reason="logout_all")
            )
            await db.commit()

    clear_refresh_token_cookie(response)
    return {"detail": "Logged out of all devices successfully"}


@router.get("/sessions")
async def get_active_sessions(
    refresh_token: str | None = Cookie(default=None),
    db: AsyncSession = Depends(get_db)
):
    if not refresh_token:
        raise HTTPException(status_code=401, detail="Not authenticated")
        
    hashed_token = hash_token(refresh_token)
    result = await db.execute(
        select(RefreshToken).where(RefreshToken.token_hash == hashed_token)
    )
    current_token = result.scalars().first()
    
    if not current_token or current_token.revoked_at:
        raise HTTPException(status_code=401, detail="Session expired or invalid")
        
    # Fetch all active sessions for this user per PRD 4.1
    sessions_result = await db.execute(
        select(RefreshToken)
        .where(RefreshToken.user_id == current_token.user_id)
        .where(RefreshToken.revoked_at.is_(None))
        .order_by(RefreshToken.created_at.desc())
    )
    
    active_sessions = sessions_result.scalars().all()
    
    return {
        "items": [
            {
                "id": str(session.id),
                "created_at": session.created_at,
                "last_used_at": session.last_used_at or session.created_at,
                "user_agent": session.user_agent,
                "ip_address": str(session.ip_address) if session.ip_address else None,
                "is_current": session.id == current_token.id
            }
            for session in active_sessions
        ]
    }