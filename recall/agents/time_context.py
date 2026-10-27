"""Runtime date/time context injected into every agent user prompt."""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from recall.config import Settings, get_settings

AGENT_TIMEZONE = ZoneInfo("America/Toronto")


def now(*, settings: Settings | None = None) -> datetime:
    """Return frozen sample clock when configured, else real local time.

    In ``sample_mode``, prefers ``settings.sample_now`` (from SAMPLE_NOW or
    dataset.json). Always returns a timezone-aware datetime.
    """
    active = settings if settings is not None else get_settings()
    if active.sample_mode:
        raw = (active.sample_now or "").strip()
        if raw:
            try:
                parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                parsed = None
            if parsed is not None:
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=AGENT_TIMEZONE)
                return parsed.astimezone(AGENT_TIMEZONE)
    return datetime.now(tz=AGENT_TIMEZONE)


def today_line(*, clock: datetime | None = None, settings: Settings | None = None) -> str:
    """Return 'TODAY: YYYY-MM-DD, Weekday, time zone America/Toronto'."""
    local = (clock if clock is not None else now(settings=settings)).astimezone(AGENT_TIMEZONE)
    weekday = local.strftime("%A")
    return f"TODAY: {local.date().isoformat()}, {weekday}, time zone America/Toronto"


def with_today(
    prompt: str,
    *,
    clock: datetime | None = None,
    settings: Settings | None = None,
) -> str:
    """Prepend the TODAY line to a user prompt."""
    return f"{today_line(clock=clock, settings=settings)}\n{prompt}"
