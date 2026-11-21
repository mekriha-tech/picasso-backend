import uuid
from sqlalchemy import Text, SmallInteger, Integer, Boolean, ForeignKey, Index, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from app.db.session import Base


class ArtworkImage(Base):
    __tablename__ = "artwork_images"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    artwork_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("artworks.id", ondelete="CASCADE"), nullable=False
    )
    url: Mapped[str] = mapped_column(Text, nullable=False)
    # Smaller derivative (~800px). NULL for rows that predate R2 storage (seeded external URLs).
    preview_url: Mapped[str | None] = mapped_column(Text)
    # Which provider holds the objects ("r2", "cloudinary", "s3"). NULL = not in our storage
    # (external URL, e.g. seeded images), so it is never deleted or migrated.
    storage_provider: Mapped[str | None] = mapped_column(Text)
    # Object keys in the store, kept so delete never has to parse them back out of a URL.
    # NULL when the image is not stored in our bucket.
    storage_key: Mapped[str | None] = mapped_column(Text)
    preview_storage_key: Mapped[str | None] = mapped_column(Text)
    alt_text: Mapped[str | None] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0")
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    width_px: Mapped[int | None] = mapped_column(Integer)
    height_px: Mapped[int | None] = mapped_column(Integer)

    __table_args__ = (
        Index("one_primary_image_per_artwork", "artwork_id", unique=True, postgresql_where=text("is_primary")),
    )
