"""Calendar boundaries in Moscow time, shared by the payroll and the support leaderboard.

Moscow has no DST, so a fixed UTC+3 offset keeps day / week / month boundaries stable
without shipping a tzdata database.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

MSK = timezone(timedelta(hours=3))

PERIODS = ("day", "week", "month")


def period_start(period: str, now: datetime) -> datetime:
    """Local (MSK) start of the current day, ISO week (Monday) or calendar month."""
    if period not in PERIODS:
        raise ValueError(period)
    start = now.astimezone(MSK).replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "week":
        start -= timedelta(days=start.weekday())
    elif period == "month":
        start = start.replace(day=1)
    return start


def period_range(period: str, now: datetime) -> tuple[datetime, datetime]:
    """Half-open UTC range from the start of the period up to ``now``."""
    return period_start(period, now).astimezone(timezone.utc), now.astimezone(timezone.utc)