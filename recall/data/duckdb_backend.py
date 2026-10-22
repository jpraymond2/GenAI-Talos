"""In-memory DuckDB backend over sample CSVs or live parquet files."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, ClassVar

import duckdb

from recall.config import Settings
from recall.data.query_layer import DataUnavailable, timed_query_event
from recall.data.sql_bind import to_named_dollar
from recall.tracing import Tracer

_TABLES = ("handles", "interactions", "events", "contacts")


class DuckDBBackend:
    """Load recall tables once into an in-memory DuckDB and run parameterized SQL."""

    _connections: ClassVar[dict[str, duckdb.DuckDBPyConnection]] = {}

    def __init__(self, settings: Settings, tracer: Tracer | None = None) -> None:
        """Bind settings and an optional tracer for query events."""
        self._settings = settings
        self._tracer = tracer
        self._backend_name = "duckdb"

    def query(
        self,
        sql: str,
        params: dict[str, Any] | None = None,
        *,
        source_tag: str,
    ) -> list[dict[str, Any]]:
        """Run one parameterized read against the in-memory recall schema."""
        started = time.perf_counter()
        try:
            bound_sql, bound_params = to_named_dollar(sql, params)
            conn = self._connection()
            result = conn.execute(bound_sql, bound_params)
            columns = [desc[0] for desc in result.description or []]
            rows = [dict(zip(columns, row, strict=True)) for row in result.fetchall()]
        except DataUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001 - surface as unavailable to callers
            raise DataUnavailable(f"DuckDB query failed: {exc}") from exc
        timed_query_event(
            self._tracer,
            backend=self._backend_name,
            source_tag=source_tag,
            sql=sql,
            row_count=len(rows),
            started=started,
        )
        return rows

    def _connection(self) -> duckdb.DuckDBPyConnection:
        key = self._cache_key()
        cached = self._connections.get(key)
        if cached is not None:
            return cached
        conn = self._load_database()
        self._connections[key] = conn
        return conn

    def _cache_key(self) -> str:
        settings = self._settings
        root = settings.sample_data_dir if settings.sample_mode else settings.live_data_dir
        return f"{settings.sample_mode}:{Path(root).resolve()}"

    def _load_database(self) -> duckdb.DuckDBPyConnection:
        settings = self._settings
        conn = duckdb.connect(database=":memory:")
        conn.execute("CREATE SCHEMA IF NOT EXISTS recall")
        if settings.sample_mode:
            self._load_csvs(conn, Path(settings.sample_data_dir))
        else:
            self._load_parquets(conn, Path(settings.live_data_dir))
        return conn

    def _load_csvs(self, conn: duckdb.DuckDBPyConnection, directory: Path) -> None:
        if not directory.is_dir():
            raise DataUnavailable(f"Sample data directory missing: {directory}")
        for table in _TABLES:
            path = directory / f"{table}.csv"
            if not path.is_file():
                raise DataUnavailable(f"Sample CSV missing: {path}")
            # Table names are fixed constants; file path is bound as a parameter.
            conn.execute(
                f"CREATE OR REPLACE TABLE recall.{table} AS "
                "SELECT * FROM read_csv_auto(?, header=true, sample_size=-1, all_varchar=true)",
                [str(path.resolve())],
            )

    def _load_parquets(self, conn: duckdb.DuckDBPyConnection, directory: Path) -> None:
        if not directory.is_dir():
            raise DataUnavailable(f"Live data directory missing: {directory}")
        for table in _TABLES:
            path = directory / f"{table}.parquet"
            if not path.is_file():
                raise DataUnavailable(f"Live parquet missing: {path}")
            conn.execute(
                f"CREATE OR REPLACE TABLE recall.{table} AS SELECT * FROM read_parquet(?)",
                [str(path.resolve())],
            )

    @classmethod
    def clear_connection_cache(cls) -> None:
        """Drop cached in-memory connections (for tests that change data dirs)."""
        for conn in cls._connections.values():
            conn.close()
        cls._connections.clear()
