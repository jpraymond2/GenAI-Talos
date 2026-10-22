"""DuckDB backend. Sample mode reads nothing live and returns no rows."""

from typing import Any

from recall.config import Settings


class DuckDBBackend:
    """Local DuckDB reads. Live queries are not wired yet."""

    def __init__(self, settings: Settings) -> None:
        """Keep settings so sample mode can skip the database file."""
        self._settings = settings

    def query(self, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Run a read. Sample mode returns no rows and does not open a database."""
        if self._settings.sample_mode:
            return []
        _ = (sql, params)
        raise NotImplementedError("DuckDB backend is not wired yet")
