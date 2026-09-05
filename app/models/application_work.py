import uuid
from sqlalchemy import Text, SmallInteger, ForeignKey, CheckConstraint, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from app.db.session import Base


class ApplicationWork(Base):
    __tablename__ = "application_works"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("artist_applications.id", ondelete="CASCADE"), nullable=False
    )
    slot_index: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    year: Mapped[int | None] = mapped_column(SmallInteger)
    medium: Mapped[str | None] = mapped_column(Text)
    dimensions: Mapped[str | None] = mapped_column(Text)
    image_url: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        CheckConstraint("slot_index BETWEEN 0 AND 2", name="application_works_slot_index_check"),
        UniqueConstraint("application_id", "slot_index", name="uq_application_works_slot"),
    )
