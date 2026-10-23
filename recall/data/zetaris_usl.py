"""USL table/column mapping shared by ZetarisBackend and setup scripts.

The live USL was compiled with renamed columns (variant ``a_renamed_columns``):
org→organisation, source→source_system, text→body, value→handle_value,
location→place. Reads in ``ZETARIS_READ_MODE=usl`` project those physical
names back to the unified schema via SELECT aliases so ``queries.py`` stays
unchanged.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from recall.data.query_layer import DataUnavailable

# Deployed USL path: lightning.metastore.recallai.recall.{contact,handle,...}
DEFAULT_USL_NAMESPACE = "lightning.metastore.recallai.recall"

# recall.<plural> → USL singular table name
RECALL_TO_USL_TABLE: dict[str, str] = {
    "contacts": "contact",
    "handles": "handle",
    "interactions": "interaction",
    "events": "event",
}

# Unified schema column → USL physical column (only renames; identity omitted).
# Keyed by USL singular table name — the same rename list used by setup.
USL_COLUMN_RENAMES: dict[str, dict[str, str]] = {
    "contact": {
        "org": "organisation",
        "source": "source_system",
    },
    "handle": {
        "value": "handle_value",
        "source": "source_system",
    },
    "interaction": {
        "source": "source_system",
        "text": "body",
    },
    "event": {
        "location": "place",
    },
}

# Unified schema column order per recall.* table (SELECT list for the USL view).
UNIFIED_COLUMNS: dict[str, tuple[str, ...]] = {
    "contacts": (
        "contact_id",
        "full_name",
        "org",
        "title",
        "city",
        "emails",
        "phones",
        "source",
    ),
    "handles": (
        "handle_id",
        "kind",
        "value",
        "display_name",
        "source",
    ),
    "interactions": (
        "interaction_id",
        "source",
        "thread_id",
        "handle_value",
        "direction",
        "ts_utc",
        "subject",
        "text",
        "source_ref",
    ),
    "events": (
        "event_id",
        "title",
        "start_utc",
        "end_utc",
        "location",
        "attendees",
        "source_ref",
    ),
}

_RECALL_TABLE = re.compile(
    r"\brecall\.(contacts|handles|interactions|events)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class UslRewriteResult:
    """Rewritten SQL plus the USL tables referenced."""

    sql: str
    usl_tables: tuple[str, ...]


def usl_fqn(recall_table: str, *, namespace: str = DEFAULT_USL_NAMESPACE) -> str:
    """Fully-qualified USL table for a recall.* plural name."""
    key = recall_table.lower()
    if key not in RECALL_TO_USL_TABLE:
        raise DataUnavailable(f"No USL mapping for recall.{recall_table}")
    return f"{namespace.rstrip('.')}.{RECALL_TO_USL_TABLE[key]}"


def physical_column(usl_table: str, unified_column: str) -> str:
    """USL physical column name for a unified schema column."""
    renames = USL_COLUMN_RENAMES.get(usl_table, {})
    return renames.get(unified_column, unified_column)


def unified_select_list(recall_table: str) -> str:
    """``phys AS unified, …`` projection that restores the recall.* schema."""
    key = recall_table.lower()
    usl_table = RECALL_TO_USL_TABLE[key]
    parts: list[str] = []
    for col in UNIFIED_COLUMNS[key]:
        phys = physical_column(usl_table, col)
        if phys == col:
            parts.append(col)
        else:
            parts.append(f"{phys} AS {col}")
    return ", ".join(parts)


def usl_subquery(recall_table: str, *, namespace: str = DEFAULT_USL_NAMESPACE) -> str:
    """Inline view: USL physical columns aliased back to unified names."""
    key = recall_table.lower()
    alias = key  # contacts / handles / …
    fqn = usl_fqn(key, namespace=namespace)
    projection = unified_select_list(key)
    return f"(SELECT {projection} FROM {fqn}) AS {alias}"


def rewrite_sql_for_usl(
    sql: str,
    *,
    namespace: str = DEFAULT_USL_NAMESPACE,
) -> UslRewriteResult:
    """Replace ``recall.contacts|handles|interactions|events`` with USL subqueries."""
    touched: list[str] = []

    def repl(match: re.Match[str]) -> str:
        recall_table = match.group(1).lower()
        fqn = usl_fqn(recall_table, namespace=namespace)
        if fqn not in touched:
            touched.append(fqn)
        return usl_subquery(recall_table, namespace=namespace)

    rewritten = _RECALL_TABLE.sub(repl, sql)
    return UslRewriteResult(sql=rewritten, usl_tables=tuple(touched))


def detect_usl_tables(
    sql: str,
    *,
    namespace: str = DEFAULT_USL_NAMESPACE,
) -> tuple[str, ...]:
    """USL FQNs that ``rewrite_sql_for_usl`` would touch (without rewriting)."""
    found: list[str] = []
    for match in _RECALL_TABLE.finditer(sql):
        fqn = usl_fqn(match.group(1), namespace=namespace)
        if fqn not in found:
            found.append(fqn)
    return tuple(found)
