import uuid
from decimal import Decimal, InvalidOperation
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_optional_user
from app.db.session import get_db
from app.models.artwork import ListingType
from app.models.user import User
from app.schemas.catalogue import (
    ArtistDetailOut,
    ArtworkDetailOut,
    FeaturedArtworksOut,
    PaginatedArtistsOut,
    PaginatedArtworksOut,
)
from app.services import catalogue as catalogue_service
from app.services.catalogue import InvalidCursorError
from app.services.teaser import (
    LOGIN_REQUIRED_MESSAGE,
    anonymous_artwork_list_violation,
    teaser_limit,
)

router = APIRouter()

SortOption = Literal["newest", "price_asc", "price_desc", "ending_soon"]


def _login_required() -> HTTPException:
    return HTTPException(
        status_code=401,
        detail=LOGIN_REQUIRED_MESSAGE,
        headers={"WWW-Authenticate": "Bearer"},
    )


@router.get("/artworks", response_model=PaginatedArtworksOut)
async def list_artworks_route(
    listing_type: ListingType | None = Query(default=None),
    category: str | None = Query(default=None),
    artist_id: uuid.UUID | None = Query(default=None),
    min_price: str | None = Query(default=None),
    max_price: str | None = Query(default=None),
    q: str | None = Query(default=None),
    sort: SortOption = Query(default="newest"),
    limit: int = Query(default=24, ge=1, le=100),
    cursor: str | None = Query(default=None),
    user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
):
    anonymous = user is None
    only_ids = None
    if anonymous:
        if anonymous_artwork_list_violation(
            category=category,
            artist_id=artist_id,
            min_price=min_price,
            max_price=max_price,
            q=q,
            sort=sort,
            cursor=cursor,
        ):
            raise _login_required()
        limit = teaser_limit(limit)
        only_ids = await catalogue_service.get_teaser_artwork_ids(db)

    try:
        min_price_dec = Decimal(min_price) if min_price is not None else None
        max_price_dec = Decimal(max_price) if max_price is not None else None
    except InvalidOperation:
        raise HTTPException(status_code=400, detail="min_price/max_price must be numeric")

    if min_price_dec is not None and not min_price_dec.is_finite():
        raise HTTPException(status_code=400, detail="min_price/max_price must be numeric")
    if max_price_dec is not None and not max_price_dec.is_finite():
        raise HTTPException(status_code=400, detail="min_price/max_price must be numeric")

    try:
        items, next_cursor = await catalogue_service.list_public_artworks(
            db,
            listing_type=listing_type,
            category=category,
            artist_id=artist_id,
            min_price=min_price_dec,
            max_price=max_price_dec,
            q=q,
            sort=sort,
            limit=limit,
            cursor=cursor,
            only_ids=only_ids,
        )
    except InvalidCursorError:
        raise HTTPException(status_code=400, detail="Invalid cursor")

    return {
        "items": items,
        "next_cursor": None if anonymous else next_cursor,
        "teaser": anonymous,
    }


@router.get("/artworks/featured", response_model=FeaturedArtworksOut)
async def featured_artworks_route(db: AsyncSession = Depends(get_db)):
    return await catalogue_service.get_featured_artworks(db)


@router.get("/artworks/{slug}", response_model=ArtworkDetailOut)
async def get_artwork_route(
    slug: str,
    user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
):
    only_ids = await catalogue_service.get_teaser_artwork_ids(db) if user is None else None
    artwork = await catalogue_service.get_artwork_by_slug(db, slug, only_ids=only_ids)
    if artwork is None:
        # An anonymous caller asking for a real work outside the teaser is told to log in;
        # a slug that does not exist at all is a plain 404 for everyone.
        if user is None and await catalogue_service.get_artwork_by_slug(db, slug) is not None:
            raise _login_required()
        raise HTTPException(status_code=404, detail="Artwork not found")
    return artwork


@router.get("/artists", response_model=PaginatedArtistsOut)
async def list_artists_route(
    limit: int = Query(default=24, ge=1, le=100),
    cursor: str | None = Query(default=None),
    user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
):
    anonymous = user is None
    if anonymous:
        if cursor is not None:
            raise _login_required()
        limit = teaser_limit(limit)
    try:
        items, next_cursor = await catalogue_service.list_artists(db, limit=limit, cursor=cursor)
    except InvalidCursorError:
        raise HTTPException(status_code=400, detail="Invalid cursor")
    return {
        "items": items,
        "next_cursor": None if anonymous else next_cursor,
        "teaser": anonymous,
    }


@router.get("/artists/{slug}", response_model=ArtistDetailOut)
async def get_artist_route(
    slug: str,
    user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
):
    only_ids = await catalogue_service.get_teaser_artwork_ids(db) if user is None else None
    artist = await catalogue_service.get_artist_by_slug(db, slug, only_ids=only_ids)
    if artist is None:
        raise HTTPException(status_code=404, detail="Artist not found")
    artist["teaser"] = user is None
    return artist
