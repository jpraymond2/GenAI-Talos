"""Gmail tool. Sample mode dry-runs and returns no messages."""

from typing import Any

from recall.config import Settings, get_settings


def search_mail(query: str, settings: Settings | None = None) -> list[dict[str, Any]]:
    """Search mail. Sample mode returns no rows and calls no API."""
    active = settings if settings is not None else get_settings()
    if active.sample_mode:
        return []
    _ = query
    raise NotImplementedError("Gmail tool is not wired yet")
