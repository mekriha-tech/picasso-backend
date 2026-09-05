import uuid
from datetime import datetime
from pydantic import BaseModel


class ArtworkOut(BaseModel):
    id: uuid.UUID
    artist_id: uuid.UUID
    title: str
    slug: str
    listing_type: str
    status: str
    primary_image_url: str | None
    created_at: datetime

    class Config:
        from_attributes = True


class ArtworkStatusUpdate(BaseModel):
    status: str
