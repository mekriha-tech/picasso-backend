"""Admin-panel user management: list/search, detail, deactivate/reactivate, promote/demote.

Guard rails live here (not in the routes): an admin can't act on their own account, and the
last active admin can never be demoted or deactivated. Every change writes an AdminAuditLog row
in the same transaction.
"""
import uuid
from dataclasses import dataclass

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.admin_audit_log import AdminAuditLog
from app.models.artist_application import ArtistApplication
from app.models.artist_profile import ArtistProfile
from app.models.artwork import Artwork
from app.models.refresh_token import RefreshToken
from app.models.user import User

PAGE_SIZE = 25
ROLE_FILTERS = ("all", "admins", "artists")

ACTION_DEACTIVATE = "deactivate"
ACTION_REACTIVATE = "reactivate"
ACTION_PROMOTE = "promote_admin"
ACTION_DEMOTE = "demote_admin"
ACTIONS = (ACTION_DEACTIVATE, ACTION_REACTIVATE, ACTION_PROMOTE, ACTION_DEMOTE)


class UserAdminError(Exception):
    """A rule blocked the change; the message is shown to the admin as-is."""


@dataclass
class UserDetail:
    user: User
    application: ArtistApplication | None
    artist_profile: ArtistProfile | None
    artwork_count: int
    active_sessions: int
    audit: list[AdminAuditLog]


def _like_escape(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def list_users(
    db: AsyncSession, *, q: str | None = None, role: str = "all", page: int = 1,
    page_size: int = PAGE_SIZE,
) -> tuple[list[User], int]:
    """Returns (users on this page, total matching). Newest accounts first."""
    stmt = select(User)
    q = (q or "").strip()
    if q:
        pattern = f"%{_like_escape(q)}%"
        stmt = stmt.where(
            or_(User.email.ilike(pattern, escape="\\"), User.full_name.ilike(pattern, escape="\\"))
        )
    if role == "admins":
        stmt = stmt.where(User.is_admin.is_(True))
    elif role == "artists":
        stmt = stmt.where(User.artist_status == "approved")
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    page = max(page, 1)
    rows = (
        await db.execute(
            stmt.order_by(User.created_at.desc(), User.id)
            .limit(page_size).offset((page - 1) * page_size)
        )
    ).scalars().all()
    return list(rows), total


async def get_user(db: AsyncSession, user_id: uuid.UUID) -> User | None:
    return (await db.execute(select(User).where(User.id == user_id))).scalars().first()


async def get_user_detail(db: AsyncSession, user_id: uuid.UUID) -> UserDetail | None:
    user = await get_user(db, user_id)
    if user is None:
        return None
    application = (
        await db.execute(
            select(ArtistApplication).where(ArtistApplication.user_id == user.id)
            .order_by(ArtistApplication.created_at.desc()).limit(1)
        )
    ).scalars().first()
    profile = (
        await db.execute(select(ArtistProfile).where(ArtistProfile.user_id == user.id))
    ).scalars().first()
    artwork_count = 0
    if profile is not None:
        artwork_count = (
            await db.execute(
                select(func.count()).select_from(Artwork)
                .where(Artwork.artist_id == profile.id, Artwork.deleted_at.is_(None))
            )
        ).scalar_one()
    active_sessions = (
        await db.execute(
            select(func.count()).select_from(RefreshToken)
            .where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
        )
    ).scalar_one()
    audit = (
        await db.execute(
            select(AdminAuditLog).where(AdminAuditLog.target_user_id == user.id)
            .order_by(AdminAuditLog.created_at.desc()).limit(20)
        )
    ).scalars().all()
    return UserDetail(user, application, profile, artwork_count, active_sessions, list(audit))


async def _active_admin_count_locked(db: AsyncSession) -> int:
    """Counts active admins while row-locking them, so two concurrent demotions can't both pass."""
    ids = (
        await db.execute(
            select(User.id).where(User.is_admin.is_(True), User.is_active.is_(True)).with_for_update()
        )
    ).scalars().all()
    return len(ids)


def _guard(actor: User, target: User, action: str) -> None:
    if action not in ACTIONS:
        raise UserAdminError("Unknown action.")
    if actor.id == target.id and action in (ACTION_DEACTIVATE, ACTION_DEMOTE):
        raise UserAdminError("You can't change your own account. Ask another admin.")


async def apply_action(db: AsyncSession, actor: User, target_id: uuid.UUID, action: str) -> User:
    """Applies one action, with guard rails, and records it. Raises UserAdminError if blocked."""
    target = (
        await db.execute(select(User).where(User.id == target_id).with_for_update())
    ).scalars().first()
    if target is None:
        raise UserAdminError("User not found.")
    _guard(actor, target, action)

    if action == ACTION_DEACTIVATE:
        if not target.is_active:
            raise UserAdminError("This account is already deactivated.")
        if target.is_admin and await _active_admin_count_locked(db) <= 1:
            raise UserAdminError("This is the last active admin and can't be deactivated.")
        target.is_active = False
        await db.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == target.id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=func.now(), revoked_reason="account_deactivated")
        )
    elif action == ACTION_REACTIVATE:
        if target.is_active:
            raise UserAdminError("This account is already active.")
        target.is_active = True
    elif action == ACTION_PROMOTE:
        if target.is_admin:
            raise UserAdminError("This user is already an admin.")
        if not target.is_active:
            raise UserAdminError("Reactivate this account before making it an admin.")
        target.is_admin = True
    elif action == ACTION_DEMOTE:
        if not target.is_admin:
            raise UserAdminError("This user is not an admin.")
        if target.is_active and await _active_admin_count_locked(db) <= 1:
            raise UserAdminError("This is the last active admin and can't be demoted.")
        target.is_admin = False

    db.add(
        AdminAuditLog(
            actor_id=actor.id, target_user_id=target.id, actor_email=str(actor.email),
            target_email=str(target.email), action=action,
        )
    )
    await db.commit()
    return target
