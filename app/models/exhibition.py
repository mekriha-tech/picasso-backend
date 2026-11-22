import enum
import uuid
from datetime import date, datetime
from sqlalchemy import (
    Text, SmallInteger, Boolean, Date, DateTime, ForeignKey, CheckConstraint, func, text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from app.db.session import Base


class ViewerKind(str, enum.Enum):
    vendor_embed = "vendor_embed"
    first_party = "first_party"


class Exhibition(Base):
    __tablename__ = "exhibitions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    slug: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    cover_image_url: Mapped[str | None] = mapped_column(Text)
    vr_tour_url: Mapped[str | None] = mapped_column(Text)
    viewer_kind: Mapped[str] = mapped_column(
        Text, nullable=False, default=ViewerKind.vendor_embed.value,
        server_default=ViewerKind.vendor_embed.value,
    )
    curator_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    starts_on: Mapped[date | None] = mapped_column(Date)
    ends_on: Mapped[date | None] = mapped_column(Date)
    is_published: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    sort_order: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "viewer_kind IN ('vendor_embed', 'first_party')", name="exhibition_viewer_kind_valid"
        ),
        CheckConstraint(
            "viewer_kind <> 'vendor_embed' OR vr_tour_url IS NOT NULL",
            name="vendor_embed_needs_vr_tour_url",
        ),
    )


class ExhibitionArtwork(Base):
    __tablename__ = "exhibition_artworks"

    exhibition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("exhibitions.id", ondelete="CASCADE"), primary_key=True
    )
    artwork_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("artworks.id", ondelete="CASCADE"), primary_key=True
    )
    wall_position: Mapped[int | None] = mapped_column(SmallInteger)
