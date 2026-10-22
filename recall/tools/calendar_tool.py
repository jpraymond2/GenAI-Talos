"""Calendar tool. Sample mode dry-runs and returns no events."""

from typing import Any

from recall.config import Settings, get_settings


def list_events(settings: Settings | None = None) -> list[dict[str, Any]]:
    """List upcoming events. Sample mode returns no rows and calls no API."""
    active = settings if settings is not None else get_settings()
    if active.sample_mode:
        return []
    raise NotImplementedError("Calendar tool is not wired yet")
