from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.testimonial import Testimonial


async def list_published_testimonials(db: AsyncSession) -> list[Testimonial]:
    """Published testimonials in display order (sort_order, then id for a stable tie-break)."""
    result = await db.execute(
        select(Testimonial)
        .where(Testimonial.is_published.is_(True))
        .order_by(Testimonial.sort_order, Testimonial.id)
    )
    return list(result.scalars().all())
