"""Route every data read to the Zetaris or DuckDB backend."""

from __future__ import annotations

import json
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


class CachingQueryLayer:
    """Deduplicate identical SQL+params within one request / layer lifetime."""

    def __init__(self, inner: QueryLayer) -> None:
        self._inner = inner
        self._cache: dict[str, list[dict[str, Any]]] = {}

    def query(
        self,
        sql: str,
        params: dict[str, Any] | None = None,
        *,
        source_tag: str,
    ) -> list[dict[str, Any]]:
        key = _query_cache_key(sql, params)
        hit = self._cache.get(key)
        if hit is not None:
            return [dict(row) for row in hit]
        rows = self._inner.query(sql, params, source_tag=source_tag)
        self._cache[key] = [dict(row) for row in rows]
        return [dict(row) for row in rows]


def _query_cache_key(sql: str, params: dict[str, Any] | None) -> str:
    return json.dumps(
        {"sql": sql.strip(), "params": params or {}},
        sort_keys=True,
        default=str,
    )


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
        if self._primary_name == "zetaris":
            from recall.data.zetaris_backend import zetaris_circuit_is_open

            if zetaris_circuit_is_open():
                if self._tracer is not None:
                    self._tracer.event(
                        "query_layer",
                        "fallback",
                        {
                            "summary": (
                                f"fallback {self._primary_name}→{self._fallback_name} "
                                f"reason=circuit_open tag={source_tag}"
                            ),
                            "from_backend": self._primary_name,
                            "to_backend": self._fallback_name,
                            "source_tag": source_tag,
                            "reason": "circuit_open",
                        },
                    )
                return self._fallback.query(sql, params, source_tag=source_tag)
        try:
            return self._primary.query(sql, params, source_tag=source_tag)
        except DataUnavailable as exc:
            if self._tracer is not None:
                self._tracer.event(
                    "query_layer",
                    "fallback",
                    {
                        "summary": (
                            f"fallback {self._primary_name}→{self._fallback_name} "
                            f"reason={exc} tag={source_tag}"
                        ),
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
        # Process-wide Zetaris backend so login token + HTTP keep-alive persist
        # across asks; wrap with a fresh per-call cache for SQL dedupe.
        primary = ZetarisBackend.shared(active, tracer=active_tracer)
        return CachingQueryLayer(FallbackQueryLayer(primary, duck, active_tracer))
    return CachingQueryLayer(duck)


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
    from recall.data.zetaris_backend import ZetarisBackend

    ZetarisBackend.clear_shared()


def timed_query_event(
    tracer: Tracer | None,
    *,
    backend: str,
    source_tag: str,
    sql: str,
    row_count: int,
    started: float,
    extra: dict[str, Any] | None = None,
) -> None:
    """Write a standard query_layer query event when a tracer is present."""
    if tracer is None:
        return
    latency_ms = (time.perf_counter() - started) * 1000.0
    ms = round(latency_ms, 3)
    payload: dict[str, Any] = {
        "summary": (
            f"query {source_tag} rows={row_count} backend={backend} ms={ms}"
        ),
        "backend": backend,
        "source_tag": source_tag,
        "sql": sql,
        "row_count": row_count,
        "latency_ms": ms,
    }
    if extra:
        payload.update(extra)
        if "summary" not in extra:
            payload["summary"] = (
                f"query {source_tag} rows={row_count} backend={backend} ms={ms}"
            )
    tracer.event("query_layer", "query", payload)
