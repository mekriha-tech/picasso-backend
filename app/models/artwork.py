import enum
import uuid
from datetime import datetime
from sqlalchemy import (
    Text, SmallInteger, Integer, Numeric, Boolean, DateTime, ForeignKey,
    Enum as SQLEnum, CheckConstraint, func, text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from app.db.session import Base


class ListingType(str, enum.Enum):
    sale = "sale"
    auction = "auction"
    display = "display"


class ArtworkStatus(str, enum.Enum):
    draft = "draft"
    published = "published"
    reserved = "reserved"
    sold = "sold"
    unlisted = "unlisted"
    removed = "removed"


class Artwork(Base):
    __tablename__ = "artworks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    artist_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("artist_profiles.id", ondelete="RESTRICT"), nullable=False
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    slug: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    year: Mapped[int | None] = mapped_column(SmallInteger)
    medium: Mapped[str | None] = mapped_column(Text)
    dimensions: Mapped[str | None] = mapped_column(Text)
    width_cm: Mapped[float | None] = mapped_column(Numeric(8, 2))
    height_cm: Mapped[float | None] = mapped_column(Numeric(8, 2))
    category: Mapped[str | None] = mapped_column(Text)
    listing_type: Mapped[ListingType] = mapped_column(
        SQLEnum(ListingType, name="listing_type"),
        default=ListingType.display,
        server_default="display",
        nullable=False,
    )
    status: Mapped[ArtworkStatus] = mapped_column(
        SQLEnum(ArtworkStatus, name="artwork_status"),
        default=ArtworkStatus.draft,
        server_default="draft",
        nullable=False,
    )
    price: Mapped[float | None] = mapped_column(Numeric(12, 2))
    sold: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    sold_price: Mapped[float | None] = mapped_column(Numeric(12, 2))
    sold_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    primary_image_url: Mapped[str | None] = mapped_column(Text)
    view_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("listing_type <> 'sale' OR price IS NOT NULL", name="sale_needs_price"),
        CheckConstraint("listing_type <> 'display' OR price IS NULL", name="display_has_no_price"),
    )
