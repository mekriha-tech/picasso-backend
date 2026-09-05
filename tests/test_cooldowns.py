from datetime import datetime, timedelta, timezone

from app.services.cooldowns import reapply_available_at, is_in_reapply_cooldown


def test_reapply_available_at_adds_30_days():
    rejected_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert reapply_available_at(rejected_at) == datetime(2026, 1, 31, tzinfo=timezone.utc)


def test_is_in_cooldown_true_the_day_after_rejection():
    rejected_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    now = rejected_at + timedelta(days=1)
    assert is_in_reapply_cooldown(rejected_at, now) is True


def test_is_in_cooldown_false_after_30_days():
    rejected_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    now = rejected_at + timedelta(days=31)
    assert is_in_reapply_cooldown(rejected_at, now) is False
