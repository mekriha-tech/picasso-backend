import uuid
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from app.models.artist_application import ArtistApplication, ApplicationStatus
from app.models.application_work import ApplicationWork
from app.models.user import User, ArtistStatus
from app.schemas.artist_application import ArtistApplicationIn, ApplicationWorkIn
from app.services.cooldowns import is_in_reapply_cooldown, reapply_available_at


class ApplicationNotEditableError(Exception):
    """Raised when trying to change an application that's no longer a draft, or no longer
    submitted/under_review (see Task 9's use of this for approve/reject/claim)."""


async def get_open_application(db: AsyncSession, user_id: uuid.UUID) -> ArtistApplication | None:
    result = await db.execute(
        select(ArtistApplication)
        .where(ArtistApplication.user_id == user_id)
        .where(ArtistApplication.status.in_([
            ApplicationStatus.draft, ApplicationStatus.submitted, ApplicationStatus.under_review,
        ]))
    )
    return result.scalars().first()


async def get_application_by_id(db: AsyncSession, application_id: uuid.UUID) -> ArtistApplication | None:
    result = await db.execute(select(ArtistApplication).where(ArtistApplication.id == application_id))
    return result.scalars().first()


async def get_application_works(db: AsyncSession, application_id: uuid.UUID) -> list[ApplicationWork]:
    result = await db.execute(
        select(ApplicationWork)
        .where(ApplicationWork.application_id == application_id)
        .order_by(ApplicationWork.slot_index)
    )
    return list(result.scalars().all())


async def create_or_update_draft(
    db: AsyncSession, user_id: uuid.UUID, data: ArtistApplicationIn
) -> ArtistApplication:
    application = await get_open_application(db, user_id)
    if application is not None and application.status != ApplicationStatus.draft:
        raise ApplicationNotEditableError("Cannot edit an application after it's been submitted.")

    if application is None:
        application = ArtistApplication(user_id=user_id)
        db.add(application)

    application.full_name = data.full_name
    application.location = data.location
    application.primary_medium = data.primary_medium
    application.years_practising = data.years_practising
    application.website_url = data.website_url
    application.instagram = data.instagram
    application.statement = data.statement

    await db.commit()
    await db.refresh(application)
    return application


async def set_application_work(
    db: AsyncSession, application: ArtistApplication, slot_index: int, data: ApplicationWorkIn
) -> ApplicationWork:
    if application.status != ApplicationStatus.draft:
        raise ApplicationNotEditableError("Cannot edit an application after it's been submitted.")

    result = await db.execute(
        select(ApplicationWork)
        .where(ApplicationWork.application_id == application.id)
        .where(ApplicationWork.slot_index == slot_index)
    )
    work = result.scalars().first()
    if work is None:
        work = ApplicationWork(application_id=application.id, slot_index=slot_index)
        db.add(work)

    work.title = data.title
    work.image_url = data.image_url
    work.year = data.year
    work.medium = data.medium
    work.dimensions = data.dimensions

    await db.commit()
    await db.refresh(work)
    return work


async def clear_application_work(db: AsyncSession, application: ArtistApplication, slot_index: int) -> None:
    if application.status != ApplicationStatus.draft:
        raise ApplicationNotEditableError("Cannot edit an application after it's been submitted.")

    result = await db.execute(
        select(ApplicationWork)
        .where(ApplicationWork.application_id == application.id)
        .where(ApplicationWork.slot_index == slot_index)
    )
    work = result.scalars().first()
    if work is not None:
        await db.delete(work)
        await db.commit()


class ApplicationValidationError(Exception):
    def __init__(self, errors: dict[str, list[str]]):
        self.errors = errors
        super().__init__(str(errors))


async def get_last_rejected_application(db: AsyncSession, user_id: uuid.UUID) -> ArtistApplication | None:
    result = await db.execute(
        select(ArtistApplication)
        .where(ArtistApplication.user_id == user_id)
        .where(ArtistApplication.status == ApplicationStatus.rejected)
        .order_by(ArtistApplication.reviewed_at.desc())
    )
    return result.scalars().first()


async def submit_application(db: AsyncSession, application: ArtistApplication) -> ArtistApplication:
    if application.status != ApplicationStatus.draft:
        raise ApplicationNotEditableError("This application has already been submitted.")

    works = await get_application_works(db, application.id)
    errors: dict[str, list[str]] = {}
    if len(works) != 3:
        errors["works"] = ["Submit three works…"]
    if not application.primary_medium:
        errors["primary_medium"] = ["Tell us your primary medium."]
    if not application.full_name:
        errors["full_name"] = ["Tell us your name."]
    if not application.location:
        errors["location"] = ["Tell us your location."]
    if errors:
        raise ApplicationValidationError(errors)

    last_rejected = await get_last_rejected_application(db, application.user_id)
    if last_rejected is not None and last_rejected.reviewed_at is not None:
        now = datetime.now(timezone.utc)
        if is_in_reapply_cooldown(last_rejected.reviewed_at, now):
            available_at = reapply_available_at(last_rejected.reviewed_at)
            raise ApplicationValidationError(
                {"detail": [f"You can reapply on {available_at.date().isoformat()}."]}
            )

    application.status = ApplicationStatus.submitted
    application.submitted_at = func.now()

    result = await db.execute(select(User).where(User.id == application.user_id))
    user = result.scalars().first()
    user.artist_status = ArtistStatus.pending

    await db.commit()
    await db.refresh(application)
    return application
