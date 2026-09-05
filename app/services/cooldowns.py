from datetime import datetime, timedelta

REAPPLY_COOLDOWN_DAYS = 30


def reapply_available_at(rejected_at: datetime) -> datetime:
    """The PRD rule-6 date after which a rejected applicant may submit again."""
    return rejected_at + timedelta(days=REAPPLY_COOLDOWN_DAYS)


def is_in_reapply_cooldown(rejected_at: datetime, now: datetime) -> bool:
    return now < reapply_available_at(rejected_at)
