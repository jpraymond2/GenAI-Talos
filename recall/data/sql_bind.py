"""Expand portable :name placeholders into backend-specific bind styles."""

from __future__ import annotations

import re
from typing import Any

# Named queries emit ANSI SQL with :name markers (see queries.py).
_PLACEHOLDER = re.compile(r":([A-Za-z_][A-Za-z0-9_]*)")


def to_qmark(sql: str, params: dict[str, Any] | None) -> tuple[str, list[Any]]:
    """Rewrite :name to ? and expand list values into multiple placeholders.

    ``IN (:items)`` with ``items=["a","b"]`` becomes ``IN (?, ?)``.
    """
    if not params:
        return sql, []

    values: list[Any] = []

    def repl(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in params:
            raise KeyError(f"Missing SQL param: {key}")
        val = params[key]
        if isinstance(val, (list, tuple)):
            if len(val) == 0:
                raise ValueError(f"Cannot expand empty list param: {key}")
            chunks: list[str] = []
            for item in val:
                values.append(item)
                chunks.append("?")
            return ", ".join(chunks)
        values.append(val)
        return "?"

    return _PLACEHOLDER.sub(repl, sql), values


def to_named_dollar(sql: str, params: dict[str, Any] | None) -> tuple[str, dict[str, Any]]:
    """Rewrite :name to $name; expand lists to $name_0, $name_1, ..."""
    if not params:
        return sql, {}

    bound: dict[str, Any] = {}

    def repl(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in params:
            raise KeyError(f"Missing SQL param: {key}")
        val = params[key]
        if isinstance(val, (list, tuple)):
            if len(val) == 0:
                raise ValueError(f"Cannot expand empty list param: {key}")
            names: list[str] = []
            for index, item in enumerate(val):
                item_key = f"{key}_{index}"
                bound[item_key] = item
                names.append(f"${item_key}")
            return ", ".join(names)
        bound[key] = val
        return f"${key}"

    return _PLACEHOLDER.sub(repl, sql), bound
