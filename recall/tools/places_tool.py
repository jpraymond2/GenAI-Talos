"""Places tool. Sample mode dry-runs and returns nothing."""

from typing import Any

from recall.config import Settings, get_settings


def lookup_place(name: str, settings: Settings | None = None) -> dict[str, Any]:
    """Look up a place. Sample mode returns an empty dict and calls no API."""
    active = settings if settings is not None else get_settings()
    if active.sample_mode:
        return {}
    _ = name
    raise NotImplementedError("Places tool is not wired yet")
