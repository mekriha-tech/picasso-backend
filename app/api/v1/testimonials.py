from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.testimonial import TestimonialsOut
from app.services import testimonials as testimonials_service

router = APIRouter()


@router.get("/testimonials", response_model=TestimonialsOut)
async def list_testimonials_route(response: Response, db: AsyncSession = Depends(get_db)):
    # PRD section 5: cache /testimonials for 60 s.
    response.headers["Cache-Control"] = "public, max-age=60"
    return {"items": await testimonials_service.list_published_testimonials(db)}
