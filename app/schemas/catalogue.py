from datetime import datetime
from pydantic import BaseModel


class ArtistEmbed(BaseModel):
    slug: str
    display_name: str


class ArtworkImageOut(BaseModel):
    url: str
    alt_text: str | None
    sort_order: int
    is_primary: bool


class ArtworkCardOut(BaseModel):
    id: str
    slug: str
    title: str
    artist: ArtistEmbed
    primary_image_url: str | None
    medium: str | None
    dimensions: str | None
    year: int | None
    listing_type: str
    status: str
    badge: str
    price: str | None
    sold: bool
    sold_price: str | None
    auction: None
    in_wishlist: bool


class ArtworkDetailOut(ArtworkCardOut):
    description: str | None
    width_cm: str | None
    height_cm: str | None
    category: str | None
    view_count: int
    images: list[ArtworkImageOut]


class PaginatedArtworksOut(BaseModel):
    items: list[ArtworkCardOut]
    next_cursor: str | None


class FeaturedArtworksOut(BaseModel):
    sale: list[ArtworkCardOut]
    auction: list[ArtworkCardOut]
    display: list[ArtworkCardOut]


class ArtistCardOut(BaseModel):
    slug: str
    display_name: str
    primary_medium: str
    cover_image_url: str | None
    is_featured: bool


class ArtistDetailOut(ArtistCardOut):
    statement: str | None
    years_practising: int | None
    website_url: str | None
    instagram: str | None
    approved_at: datetime
    works: list[ArtworkCardOut]


class PaginatedArtistsOut(BaseModel):
    items: list[ArtistCardOut]
    next_cursor: str | None
