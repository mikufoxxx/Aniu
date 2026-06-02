from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo


BEIJING_TZ = ZoneInfo("Asia/Shanghai")


def now_beijing() -> datetime:
    return datetime.now(BEIJING_TZ)


def to_beijing(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(BEIJING_TZ)


def beijing_iso(value: datetime | None, *, timespec: str = "seconds") -> str | None:
    local_value = to_beijing(value)
    if local_value is None:
        return None
    return local_value.isoformat(timespec=timespec)
