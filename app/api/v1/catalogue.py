import uuid
from decimal import Decimal, InvalidOperation
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.catalogue import (
    ArtistDetailOut,
    ArtworkDetailOut,
    FeaturedArtworksOut,
    PaginatedArtistsOut,
    PaginatedArtworksOut,
)
from app.services import catalogue as catalogue_service
from app.services.catalogue import InvalidCursorError

router = APIRouter()


@router.get("/artworks", response_model=PaginatedArtworksOut)
async def list_artworks_route(
    listing_type: str | None = Query(default=None),
    category: str | None = Query(default=None),
    artist_id: uuid.UUID | None = Query(default=None),
    min_price: str | None = Query(default=None),
    max_price: str | None = Query(default=None),
    q: str | None = Query(default=None),
    sort: str = Query(default="newest"),
    limit: int = Query(default=24, ge=1, le=100),
    cursor: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    try:
        min_price_dec = Decimal(min_price) if min_price is not None else None
        max_price_dec = Decimal(max_price) if max_price is not None else None
    except InvalidOperation:
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
        )
    except InvalidCursorError:
        raise HTTPException(status_code=400, detail="Invalid cursor")

    return {"items": items, "next_cursor": next_cursor}


@router.get("/artworks/featured", response_model=FeaturedArtworksOut)
async def featured_artworks_route(db: AsyncSession = Depends(get_db)):
    return await catalogue_service.get_featured_artworks(db)


@router.get("/artworks/{slug}", response_model=ArtworkDetailOut)
async def get_artwork_route(slug: str, db: AsyncSession = Depends(get_db)):
    artwork = await catalogue_service.get_artwork_by_slug(db, slug)
    if artwork is None:
        raise HTTPException(status_code=404, detail="Artwork not found")
    return artwork


@router.get("/artists", response_model=PaginatedArtistsOut)
async def list_artists_route(
    limit: int = Query(default=24, ge=1, le=100),
    cursor: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    try:
        items, next_cursor = await catalogue_service.list_artists(db, limit=limit, cursor=cursor)
    except InvalidCursorError:
        raise HTTPException(status_code=400, detail="Invalid cursor")
    return {"items": items, "next_cursor": next_cursor}


@router.get("/artists/{slug}", response_model=ArtistDetailOut)
async def get_artist_route(slug: str, db: AsyncSession = Depends(get_db)):
    artist = await catalogue_service.get_artist_by_slug(db, slug)
    if artist is None:
        raise HTTPException(status_code=404, detail="Artist not found")
    return artist
