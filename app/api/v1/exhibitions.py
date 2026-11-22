from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_optional_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.exhibition import ExhibitionDetailOut, ExhibitionsOut
from app.services import catalogue as catalogue_service
from app.services import exhibitions as exhibitions_service

router = APIRouter()


@router.get("/exhibitions", response_model=ExhibitionsOut)
async def list_exhibitions_route(db: AsyncSession = Depends(get_db)):
    return {"items": await exhibitions_service.list_published_exhibitions(db)}


# exclude_unset: the service only sets the key that applies to the viewer kind, so the other
# (vr_tour_url or artworks) is truly absent rather than null. Nullable keys that are set stay.
@router.get(
    "/exhibitions/{slug}",
    response_model=ExhibitionDetailOut,
    response_model_exclude_unset=True,
)
async def get_exhibition_route(
    slug: str,
    user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
):
    only_ids = await catalogue_service.get_teaser_artwork_ids(db) if user is None else None
    exhibition = await exhibitions_service.get_published_exhibition(db, slug, only_ids=only_ids)
    if exhibition is None:
        raise HTTPException(status_code=404, detail="Exhibition not found")
    # The teaser only applies to first-party galleries; vendor galleries are never limited.
    exhibition["teaser"] = user is None and exhibition["viewer_kind"] == "first_party"
    return exhibition
