import enum
import uuid
from datetime import datetime
from sqlalchemy import String, Text, Boolean, DateTime, func, Enum as SQLEnum, text
from sqlalchemy.dialects.postgresql import UUID, CITEXT
from sqlalchemy.orm import Mapped, mapped_column
from app.db.session import Base

# Define the enum exactly as outlined in the PRD
class ArtistStatus(str, enum.Enum):
    none = "none"
    pending = "pending"
    approved = "approved"
    rejected = "rejected"

class User(Base):
    __tablename__ = "users"

    # Updated: Added database-level gen_random_uuid() default
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), 
        primary_key=True, 
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()")
    )
    
    # Updated: Changed String to CITEXT to make emails case-insensitive
    email: Mapped[str] = mapped_column(CITEXT, unique=True, nullable=False, index=True)
    password_hash: Mapped[str | None] = mapped_column(Text)
    full_name: Mapped[str] = mapped_column(Text, nullable=False)
    avatar_url: Mapped[str | None] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(Text)
    
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    
    # Updated: Added index=True per PRD 3.2 requirements
    artist_status: Mapped[ArtistStatus] = mapped_column(
        SQLEnum(ArtistStatus, name="artist_status"), 
        default=ArtistStatus.none, 
        server_default="none",
        index=True
    )
    
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), 
        server_default=func.now(), 
        onupdate=func.now()
    )