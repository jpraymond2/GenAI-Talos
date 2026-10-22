"""Zetaris backend. Sample mode never contacts the server."""

from typing import Any

from recall.config import Settings


class ZetarisBackend:
    """Remote Zetaris reads. Live queries are not wired yet."""

    def __init__(self, settings: Settings) -> None:
        """Keep settings so sample mode can skip the Zetaris URL and key."""
        self._settings = settings

    def query(self, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Run a read. Sample mode returns no rows and does not open a connection."""
        if self._settings.sample_mode:
            return []
        _ = (sql, params)
        raise NotImplementedError("Zetaris backend is not wired yet")
