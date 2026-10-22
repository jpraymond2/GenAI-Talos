"""Runtime date/time context injected into every agent user prompt."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

AGENT_TIMEZONE = ZoneInfo("America/Toronto")


def today_line(*, now: datetime | None = None) -> str:
    """Return 'TODAY: YYYY-MM-DD, Weekday, time zone America/Toronto'."""
    local = (now or datetime.now(tz=AGENT_TIMEZONE)).astimezone(AGENT_TIMEZONE)
    weekday = local.strftime("%A")
    return f"TODAY: {local.date().isoformat()}, {weekday}, time zone America/Toronto"


def with_today(prompt: str, *, now: datetime | None = None) -> str:
    """Prepend the TODAY line to a user prompt."""
    return f"{today_line(now=now)}\n{prompt}"
