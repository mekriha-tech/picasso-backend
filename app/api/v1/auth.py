import uuid
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, status, Response, Request, Cookie
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from sqlalchemy.sql import func
from sqlalchemy.exc import IntegrityError

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.models.refresh_token import RefreshToken
from app.schemas.user import (
    UserCreate,
    RegisterResponse,
    EmailCheckRequest,
    EmailCheckResponse,
    LoginRequest,
    MeResponse,
)
from app.core.security import (
    get_password_hash,
    verify_password,
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


def _new_refresh_token_row(user_id: uuid.UUID, request: Request) -> tuple[str, RefreshToken]:
    """Builds a fresh, unsaved RefreshToken row (new session/family) plus its raw token.

    Shared by /register and /login so both start a session identically; the
    caller is responsible for db.add()-ing the row and committing it.
    """
    raw_token = generate_opaque_token()
    db_token = RefreshToken(
        user_id=user_id,
        token_hash=hash_token(raw_token),
        family_id=uuid.uuid4(),
        expires_at=datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_TTL_DAYS),
        user_agent=request.headers.get("user-agent"),
        ip_address=request.client.host if request.client else None,
    )
    return raw_token, db_token


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
    raw_refresh_token, db_refresh_token = _new_refresh_token_row(new_user.id, request)
    db.add(db_refresh_token)
    await db.commit()
    await db.refresh(new_user)

    # 6. Set httpOnly cookie for refresh token using the RAW token
    set_refresh_token_cookie(response, raw_refresh_token)

    return {
        "user": new_user,
        "access_token": access_token
    }


@router.post("/login", response_model=RegisterResponse)
async def login(
    credentials: LoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(User).where(User.email == credentials.email))
    user = result.scalars().first()

    # Same generic error whether the email is unknown or the password is
    # wrong, and whether the account has no password at all (OAuth-only,
    # per PRD §3.2's note that password_hash is nullable) - don't let a
    # timing/response difference reveal which case it was.
    invalid_credentials = HTTPException(status_code=401, detail="Invalid email or password")
    if not user or not user.password_hash:
        raise invalid_credentials
    if not verify_password(credentials.password, user.password_hash):
        raise invalid_credentials

    access_token = create_access_token(subject=str(user.id))
    raw_refresh_token, db_refresh_token = _new_refresh_token_row(user.id, request)
    db.add(db_refresh_token)

    user.last_login_at = func.now()
    await db.commit()
    await db.refresh(user)

    set_refresh_token_cookie(response, raw_refresh_token)

    return {
        "user": user,
        "access_token": access_token
    }


@router.get("/me", response_model=MeResponse)
async def get_me(current_user: User = Depends(get_current_user)):
    return {
        "id": current_user.id,
        "email": current_user.email,
        "full_name": current_user.full_name,
        "avatar_url": current_user.avatar_url,
        "is_admin": current_user.is_admin,
        "artist_status": current_user.artist_status,
        "artist_profile_id": None,
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
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == current_user.id)
        .where(RefreshToken.revoked_at.is_(None))
        .values(revoked_at=func.now(), revoked_reason="logout_all")
    )
    await db.commit()

    clear_refresh_token_cookie(response)
    return {"detail": "Logged out of all devices successfully"}


@router.get("/sessions")
async def get_active_sessions(
    current_user: User = Depends(get_current_user),
    refresh_token: str | None = Cookie(default=None),
    db: AsyncSession = Depends(get_db)
):
    # The access token (Bearer) is what authenticates this request per PRD
    # §4.1; the refresh cookie is read only, best-effort, to flag which row
    # is *this* browser's own session - its absence (e.g. a non-browser API
    # client) just means no item comes back with is_current=true.
    current_hash = hash_token(refresh_token) if refresh_token else None

    sessions_result = await db.execute(
        select(RefreshToken)
        .where(RefreshToken.user_id == current_user.id)
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
                "is_current": current_hash is not None and session.token_hash == current_hash,
            }
            for session in active_sessions
        ]
    }


@router.delete("/sessions/{session_id}")
async def revoke_session(
    session_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        update(RefreshToken)
        .where(RefreshToken.id == session_id)
        .where(RefreshToken.user_id == current_user.id)
        .where(RefreshToken.revoked_at.is_(None))
        .values(revoked_at=func.now(), revoked_reason="user_revoked")
    )
    await db.commit()

    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Session not found")

    return {"detail": "Session revoked"}

@router.post("/check-email", response_model=EmailCheckResponse)
async def check_email_exists(
    payload: EmailCheckRequest,
    db: AsyncSession = Depends(get_db)
):
    # Query the database to see if a user with this email already exists
    result = await db.execute(select(User).where(User.email == payload.email))
    user = result.scalars().first()
    
    # Return true if user exists, false if they don't
    return {"exists": user is not None}