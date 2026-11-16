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


class ArtworkImageOut(BaseModel):
    id: uuid.UUID
    artwork_id: uuid.UUID
    url: str
    preview_url: str | None
    alt_text: str | None
    sort_order: int
    is_primary: bool
    width_px: int | None
    height_px: int | None

    class Config:
        from_attributes = True


class ImageOrderUpdate(BaseModel):
    image_ids: list[uuid.UUID]
