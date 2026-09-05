from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

NEW_YORK = ZoneInfo("America/New_York")
FRIDAY = 4
ENTRY_CUTOFF = time(16, 0)
FORCE_CLOSE = time(16, 30)


def _new_york(now_utc: datetime) -> datetime:
    if now_utc.tzinfo is None:
        raise ValueError("now_utc must be timezone-aware")
    return now_utc.astimezone(NEW_YORK)


def new_entries_allowed(now_utc: datetime) -> bool:
    local = _new_york(now_utc)
    weekday = local.weekday()
    if weekday >= 5:
        return False
    if weekday == FRIDAY and local.time().replace(tzinfo=None) >= ENTRY_CUTOFF:
        return False
    return True


def weekend_force_close_due(now_utc: datetime) -> bool:
    local = _new_york(now_utc)
    return local.weekday() == FRIDAY and local.time().replace(tzinfo=None) >= FORCE_CLOSE
