from datetime import date

from pydantic import BaseModel

from app.schemas.catalogue import ArtworkCardOut


class ExhibitionCardOut(BaseModel):
    id: str
    title: str
    slug: str
    cover_image_url: str | None
    viewer_kind: str


class ExhibitionArtworkOut(ArtworkCardOut):
    wall_position: int | None
    width_cm: str | None
    height_cm: str | None
    texture_url: str | None
    image_width_px: int | None
    image_height_px: int | None


class ExhibitionDetailOut(ExhibitionCardOut):
    description: str | None
    starts_on: date | None
    ends_on: date | None
    # Exactly one of these is present, by viewer_kind; the route drops the other key.
    vr_tour_url: str | None = None
    artworks: list[ExhibitionArtworkOut] | None = None
    teaser: bool = False


class ExhibitionsOut(BaseModel):
    items: list[ExhibitionCardOut]
