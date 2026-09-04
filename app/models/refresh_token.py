import uuid
from datetime import datetime
from sqlalchemy import Text, ForeignKey, DateTime, func, text, Index
from sqlalchemy.dialects.postgresql import UUID, INET
from sqlalchemy.orm import Mapped, mapped_column
from app.db.session import Base

class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), 
        primary_key=True, 
        default=uuid.uuid4, 
        server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), 
        ForeignKey("users.id", ondelete="CASCADE"), 
        nullable=False
    )
    token_hash: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    family_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), 
        ForeignKey("refresh_tokens.id", ondelete="SET NULL")
    )
    
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_reason: Mapped[str | None] = mapped_column(Text)
    user_agent: Mapped[str | None] = mapped_column(Text)
    ip_address: Mapped[str | None] = mapped_column(INET)
    
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), 
        server_default=func.now(), 
        nullable=False
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("refresh_tokens_family_idx", "family_id"),
        Index("refresh_tokens_user_idx", "user_id", postgresql_where=text("revoked_at IS NULL")),
        Index("refresh_tokens_expiry_idx", "expires_at", postgresql_where=text("revoked_at IS NULL")),
    )