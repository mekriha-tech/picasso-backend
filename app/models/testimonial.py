import uuid
from sqlalchemy import Text, SmallInteger, Boolean, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from app.db.session import Base


class Testimonial(Base):
    __tablename__ = "testimonials"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    author_name: Mapped[str] = mapped_column(Text, nullable=False)
    author_role: Mapped[str | None] = mapped_column(Text)
    avatar_url: Mapped[str | None] = mapped_column(Text)
    quote: Mapped[str] = mapped_column(Text, nullable=False)
    sort_order: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default="0")
    is_published: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
