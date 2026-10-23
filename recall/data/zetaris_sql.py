"""Client-side Spark SQL rendering for Zetaris (no server-side bind params)."""

from __future__ import annotations

import math
import re
from typing import Any

from recall.data.query_layer import DataUnavailable
from recall.data.zetaris_usl import UslRewriteResult, rewrite_sql_for_usl

_PLACEHOLDER = re.compile(r":([A-Za-z_][A-Za-z0-9_]*)")
_RECALL_TABLE = re.compile(r"\brecall\.([A-Za-z_][A-Za-z0-9_]*)")


def sql_literal(value: Any) -> str:
    """Render one bound value as a Spark SQL literal. Rejects unsafe types."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise DataUnavailable("Zetaris SQL rejects non-finite floats")
        # Avoid locale surprises; Spark accepts plain decimal / scientific form.
        return repr(value)
    if isinstance(value, str):
        # Strict: only escape single quotes by doubling; wrap in single quotes.
        return "'" + value.replace("'", "''") + "'"
    if isinstance(value, (list, tuple)):
        raise DataUnavailable("Nested lists are not valid SQL scalars")
    raise DataUnavailable(
        f"Zetaris SQL param type not allowed: {type(value).__name__}"
    )


def render_params(sql: str, params: dict[str, Any] | None) -> str:
    """Substitute ``:name`` placeholders with escaped literals (lists → IN lists)."""
    if not params:
        return sql

    def repl(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in params:
            raise DataUnavailable(f"Missing SQL param: {key}")
        val = params[key]
        if isinstance(val, (list, tuple)):
            if len(val) == 0:
                raise DataUnavailable(f"Cannot expand empty list param: {key}")
            parts: list[str] = []
            for item in val:
                if isinstance(item, (list, tuple)):
                    raise DataUnavailable("Zetaris SQL rejects nested list params")
                if item is not None and not isinstance(item, (str, int, float, bool)):
                    raise DataUnavailable(
                        f"Zetaris SQL list item type not allowed: {type(item).__name__}"
                    )
                parts.append(sql_literal(item))
            return ", ".join(parts)
        if val is not None and not isinstance(val, (str, int, float, bool)):
            raise DataUnavailable(
                f"Zetaris SQL param type not allowed: {type(val).__name__}"
            )
        return sql_literal(val)

    try:
        return _PLACEHOLDER.sub(repl, sql)
    except DataUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001
        raise DataUnavailable(f"Zetaris SQL render failed: {type(exc).__name__}") from exc


def qualify_recall_tables(sql: str, schema: str) -> str:
    """Map ``recall.<table>`` to ``<schema>.<table>`` for Spark/Lightning."""
    schema = (schema or "recall").strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", schema):
        raise DataUnavailable("ZETARIS_SCHEMA must be a simple identifier")
    return _RECALL_TABLE.sub(rf"{schema}.\1", sql)


def prepare_zetaris_sql(
    sql: str,
    params: dict[str, Any] | None,
    *,
    schema: str = "recall",
    read_mode: str = "tables",
    usl_namespace: str = "lightning.metastore.recallai.recall",
) -> tuple[str, UslRewriteResult | None]:
    """Qualify or USL-rewrite tables, bind params, strip trailing semicolon.

    Returns ``(rendered_sql, usl_rewrite_or_none)``.
    """
    usl_meta: UslRewriteResult | None = None
    if (read_mode or "").strip().lower() == "usl":
        usl_meta = rewrite_sql_for_usl(sql, namespace=usl_namespace)
        text = usl_meta.sql
    else:
        text = qualify_recall_tables(sql, schema)
    text = render_params(text, params)
    text = text.strip().rstrip(";").strip()
    if not text:
        raise DataUnavailable("Zetaris SQL is empty after render")
    # Reject a second statement terminator outside of string literals.
    if _has_statement_separator(text):
        raise DataUnavailable("Zetaris accepts one statement per request (no ';')")
    return text, usl_meta


def _has_statement_separator(sql: str) -> bool:
    """True when a ';' appears outside of single-quoted string literals."""
    in_string = False
    i = 0
    while i < len(sql):
        ch = sql[i]
        if in_string:
            if ch == "'" and i + 1 < len(sql) and sql[i + 1] == "'":
                i += 2
                continue
            if ch == "'":
                in_string = False
            i += 1
            continue
        if ch == "'":
            in_string = True
            i += 1
            continue
        if ch == ";":
            return True
        i += 1
    return False
