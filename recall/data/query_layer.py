"""Route every data read to the Zetaris or DuckDB backend."""

from __future__ import annotations

import time
from typing import Any, Protocol

from recall.config import DataBackend, Settings, get_settings
from recall.tracing import Tracer


class DataUnavailable(Exception):
    """Raised when a backend cannot serve a read (missing config, outage, bad data)."""


class QueryLayer(Protocol):
    """The only API agents use to read source data."""

    def query(
        self,
        sql: str,
        params: dict[str, Any] | None = None,
        *,
        source_tag: str,
    ) -> list[dict[str, Any]]:
        """Run one read and return rows as dictionaries."""
        ...


class FallbackQueryLayer:
    """Try a primary backend; on DataUnavailable switch to DuckDB and trace fallback."""

    def __init__(
        self,
        primary: QueryLayer,
        fallback: QueryLayer,
        tracer: Tracer | None,
        *,
        primary_name: str = "zetaris",
        fallback_name: str = "duckdb",
    ) -> None:
        """Keep primary, DuckDB fallback, and the tracer used for fallback events."""
        self._primary = primary
        self._fallback = fallback
        self._tracer = tracer
        self._primary_name = primary_name
        self._fallback_name = fallback_name

    def query(
        self,
        sql: str,
        params: dict[str, Any] | None = None,
        *,
        source_tag: str,
    ) -> list[dict[str, Any]]:
        """Run on the primary backend, or DuckDB after a traced fallback."""
        try:
            return self._primary.query(sql, params, source_tag=source_tag)
        except DataUnavailable as exc:
            if self._tracer is not None:
                self._tracer.event(
                    "query_layer",
                    "fallback",
                    {
                        "from_backend": self._primary_name,
                        "to_backend": self._fallback_name,
                        "source_tag": source_tag,
                        "reason": str(exc),
                    },
                )
            return self._fallback.query(sql, params, source_tag=source_tag)


_default_layer: QueryLayer | None = None


def get_query_layer(
    settings: Settings | None = None,
    tracer: Tracer | None = None,
) -> QueryLayer:
    """Build the configured query layer (DuckDB, or Zetaris with DuckDB fallback)."""
    active = settings if settings is not None else get_settings()
    active_tracer = tracer if tracer is not None else Tracer.current()

    from recall.data.duckdb_backend import DuckDBBackend
    from recall.data.zetaris_backend import ZetarisBackend

    duck = DuckDBBackend(active, tracer=active_tracer)
    if active.data_backend is DataBackend.ZETARIS:
        primary = ZetarisBackend(active, tracer=active_tracer)
        return FallbackQueryLayer(primary, duck, active_tracer)
    return duck


def get_default_query_layer() -> QueryLayer:
    """Reuse one process-wide layer (and its Tracer) when callers omit ``layer``."""
    global _default_layer
    if _default_layer is None:
        _default_layer = get_query_layer()
    return _default_layer


def clear_default_query_layer() -> None:
    """Drop the cached default layer (tests that change settings/env)."""
    global _default_layer
    _default_layer = None


def timed_query_event(
    tracer: Tracer | None,
    *,
    backend: str,
    source_tag: str,
    sql: str,
    row_count: int,
    started: float,
) -> None:
    """Write a standard query_layer query event when a tracer is present."""
    if tracer is None:
        return
    latency_ms = (time.perf_counter() - started) * 1000.0
    tracer.event(
        "query_layer",
        "query",
        {
            "backend": backend,
            "source_tag": source_tag,
            "sql": sql,
            "row_count": row_count,
            "latency_ms": round(latency_ms, 3),
        },
    )
