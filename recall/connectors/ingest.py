"""Merge connector outputs and write data/live/<table>.parquet."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb

from recall.config import Settings, get_settings
from recall.connectors.gcal import fetch_gcal
from recall.connectors.gmail import fetch_gmail
from recall.connectors.imessage import fetch_imessage
from recall.connectors.schema import TABLE_COLUMNS, ConnectorResult
from recall.connectors.whatsapp import fetch_whatsapp
from recall.tracing import Tracer

_ID_KEYS = {
    "handles": "handle_id",
    "interactions": "interaction_id",
    "events": "event_id",
    "contacts": "contact_id",
}


def merge_results(results: list[ConnectorResult]) -> dict[str, list[dict[str, Any]]]:
    """Union rows from every connector, deduping on the table primary id."""
    merged: dict[str, dict[str, dict[str, Any]]] = {name: {} for name in TABLE_COLUMNS}
    for result in results:
        buckets = {
            "handles": result.handles,
            "interactions": result.interactions,
            "events": result.events,
            "contacts": result.contacts,
        }
        for table, rows in buckets.items():
            key_name = _ID_KEYS[table]
            for row in rows:
                key = str(row.get(key_name) or "")
                if not key:
                    continue
                normalized = {col: row.get(col, "") for col in TABLE_COLUMNS[table]}
                # Prefer first-seen row for stable merges.
                merged[table].setdefault(key, normalized)
    return {table: list(rows.values()) for table, rows in merged.items()}


def write_live_parquet(
    tables: dict[str, list[dict[str, Any]]],
    output_dir: Path,
) -> dict[str, int]:
    """Write the four recall tables as parquet under ``output_dir``."""
    output_dir.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    conn = duckdb.connect(database=":memory:")
    try:
        for table, columns in TABLE_COLUMNS.items():
            rows = tables.get(table) or []
            counts[table] = len(rows)
            path = output_dir / f"{table}.parquet"
            col_sql = ", ".join(f'"{col}" VARCHAR' for col in columns)
            conn.execute(f'CREATE OR REPLACE TABLE "{table}" ({col_sql})')
            if rows:
                placeholders = ", ".join("?" for _ in columns)
                values = [[str(row.get(col, "") or "") for col in columns] for row in rows]
                conn.executemany(
                    f'INSERT INTO "{table}" VALUES ({placeholders})',
                    values,
                )
            # Path comes from settings.live_data_dir (not user SQL). Escape quotes.
            dest = str(path.resolve()).replace("'", "''")
            conn.execute(f"COPY \"{table}\" TO '{dest}' (FORMAT PARQUET)")
    finally:
        conn.close()
    return counts


def run_live_ingest(
    settings: Settings | None = None,
    tracer: Tracer | None = None,
) -> dict[str, Any]:
    """Run all four connectors, write parquet, return per-source and total counts."""
    active = settings if settings is not None else get_settings()
    active_tracer = tracer if tracer is not None else Tracer.current()

    results = [
        fetch_gmail(active, active_tracer),
        fetch_gcal(active, active_tracer),
        fetch_whatsapp(active, active_tracer),
        fetch_imessage(active, active_tracer),
    ]
    tables = merge_results(results)
    totals = write_live_parquet(tables, Path(active.live_data_dir))

    per_source = {
        result.name: {
            **result.counts(),
            "skipped": result.skipped,
        }
        for result in results
    }
    summary = {"per_source": per_source, "totals": totals}
    if active_tracer is not None:
        # Counts only — never message text, emails, or tokens.
        active_tracer.event(
            "ingest",
            "live",
            {
                "totals": totals,
                "per_source": {
                    name: {
                        "handles": info["handles"],
                        "interactions": info["interactions"],
                        "events": info["events"],
                        "contacts": info["contacts"],
                        "skipped": bool(info["skipped"]),
                    }
                    for name, info in per_source.items()
                },
            },
        )
    return summary
