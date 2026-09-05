import uuid
from datetime import datetime
from pydantic import BaseModel


class ApplicationWorkIn(BaseModel):
    title: str
    image_url: str
    year: int | None = None
    medium: str | None = None
    dimensions: str | None = None


class ApplicationWorkOut(BaseModel):
    slot_index: int
    title: str
    year: int | None
    medium: str | None
    dimensions: str | None
    image_url: str


class ArtistApplicationIn(BaseModel):
    full_name: str
    location: str
    primary_medium: str
    years_practising: int | None = None
    website_url: str | None = None
    instagram: str | None = None
    statement: str | None = None


class ArtistApplicationOut(BaseModel):
    id: uuid.UUID
    status: str
    full_name: str
    location: str
    primary_medium: str
    years_practising: int | None
    website_url: str | None
    instagram: str | None
    statement: str | None
    submitted_at: datetime | None
    reviewed_at: datetime | None
    rejection_reason: str | None
    works: list[ApplicationWorkOut]


class ArtistApplicationAdminOut(ArtistApplicationOut):
    user_id: uuid.UUID
    applicant_email: str


class RejectRequest(BaseModel):
    reason: str
