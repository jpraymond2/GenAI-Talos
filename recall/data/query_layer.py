"""Route every data read to the Zetaris or DuckDB backend."""

from typing import Any, Protocol

from recall.config import DataBackend, Settings, get_settings
from recall.data.duckdb_backend import DuckDBBackend
from recall.data.zetaris_backend import ZetarisBackend


class QueryBackend(Protocol):
    """A read-only source backend."""

    def query(self, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Run one read and return rows."""
        ...


class QueryLayer:
    """The only API agents use to read source data."""

    def __init__(self, settings: Settings | None = None) -> None:
        """Select Zetaris or DuckDB from settings."""
        self._settings = settings if settings is not None else get_settings()
        self._backend = _build_backend(self._settings)

    def query(self, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Run a read. Sample mode never opens live credentials or source files from here."""
        return self._backend.query(sql, params)


def get_query_layer(settings: Settings | None = None) -> QueryLayer:
    """Build a query layer for the current settings."""
    return QueryLayer(settings)


def _build_backend(settings: Settings) -> QueryBackend:
    if settings.data_backend is DataBackend.ZETARIS:
        return ZetarisBackend(settings)
    return DuckDBBackend(settings)
