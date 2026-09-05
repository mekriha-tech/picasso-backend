import uuid
from datetime import datetime
from sqlalchemy import Text, SmallInteger, Boolean, DateTime, ForeignKey, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from app.db.session import Base


class ArtistProfile(Base):
    __tablename__ = "artist_profiles"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    slug: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    primary_medium: Mapped[str] = mapped_column(Text, nullable=False)
    years_practising: Mapped[int | None] = mapped_column(SmallInteger)
    statement: Mapped[str | None] = mapped_column(Text)
    website_url: Mapped[str | None] = mapped_column(Text)
    instagram: Mapped[str | None] = mapped_column(Text)
    cover_image_url: Mapped[str | None] = mapped_column(Text)
    is_featured: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
