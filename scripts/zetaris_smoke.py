"""Live smoke check: auth, counts, named-query parity (USL / tables / DuckDB).

Never prints tokens or passwords. Loads settings from .env via recall.config.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from recall.config import DataBackend, Settings, ZetarisReadMode, get_settings  # noqa: E402
from recall.data.duckdb_backend import DuckDBBackend  # noqa: E402
from recall.data.queries import (  # noqa: E402
    SAMPLE_ANCHOR,
    all_contacts,
    all_events_in_window,
    all_handles,
    all_interactions,
    contact_cards,
    find_handles_by_name,
    interactions_for_handles,
    upcoming_and_past_events,
)
from recall.data.query_layer import DataUnavailable  # noqa: E402
from recall.data.zetaris_backend import ZetarisBackend  # noqa: E402

_KARIM_EMAILS = ["karim@ledgerly.io", "alex.martin@mail.mcgill.ca"]

_SAMPLE_TABLE_FILES = {
    "handles": "handles.csv",
    "interactions": "interactions.csv",
    "events": "events.csv",
    "contacts": "contacts.csv",
}


def _expected_counts_from_sample(sample_dir: Path) -> dict[str, int]:
    """Count data rows in data/sample/*.csv (header excluded)."""
    import csv

    counts: dict[str, int] = {}
    for table, filename in _SAMPLE_TABLE_FILES.items():
        path = sample_dir / filename
        if not path.is_file():
            raise FileNotFoundError(f"missing sample CSV: {path}")
        with path.open(encoding="utf-8", newline="") as fh:
            rows = list(csv.reader(fh))
        # Subtract header when present.
        counts[table] = max(0, len(rows) - 1) if rows else 0
    return counts


def main() -> int:
    get_settings.cache_clear()
    settings = get_settings()
    print("Zetaris smoke")
    print(f"  REST={settings.zetaris_rest_url}")
    print(f"  BASE={settings.zetaris_base_url}")
    print(f"  ORG={settings.zetaris_org_id} SCHEMA={settings.zetaris_schema}")
    print(f"  SAMPLE_MODE={settings.sample_mode} DATA_BACKEND={settings.data_backend}")
    print(f"  ZETARIS_READ_MODE={settings.zetaris_read_mode}")
    print()

    results: list[tuple[str, str, str]] = []
    overall_ok = True

    sample_dir = Path(settings.sample_data_dir)
    try:
        expected_counts = _expected_counts_from_sample(sample_dir)
    except FileNotFoundError as exc:
        print(f"FAIL: {exc}")
        return 1
    print(
        "Expected counts from "
        f"{sample_dir}: "
        + ", ".join(f"{k}={v}" for k, v in expected_counts.items())
    )
    print()

    auth_label, base_backend = _probe_auth(settings)
    results.append(("auth", auth_label, "PASS" if auth_label != "FAIL" else "FAIL"))
    if auth_label == "FAIL":
        _print_table(results)
        return 1

    usl = ZetarisBackend(
        base_backend._settings.model_copy(
            update={"zetaris_read_mode": ZetarisReadMode.USL}
        )
    )
    # Reuse cached login token from the probe backend.
    usl._cached_token = base_backend._cached_token
    usl._auth_method = base_backend._auth_method

    tables = ZetarisBackend(
        base_backend._settings.model_copy(
            update={"zetaris_read_mode": ZetarisReadMode.TABLES}
        )
    )
    tables._cached_token = base_backend._cached_token
    tables._auth_method = base_backend._auth_method

    # Row counts against raw FILESTORE tables (stable expected sizes).
    for table, expected in expected_counts.items():
        try:
            got = _count(tables, table)
            status = "PASS" if got == expected else "FAIL"
            if status == "FAIL":
                overall_ok = False
            results.append((f"count.tables.{table}", f"{got} (want {expected})", status))
            if table == "interactions" and got != expected:
                print(
                    f"NOTE: interactions count is {got}, not {expected}. "
                    "CSV may need different multiLine/escape options — recreate the table."
                )
        except DataUnavailable as exc:
            overall_ok = False
            results.append((f"count.tables.{table}", str(exc)[:80], "FAIL"))

    # USL counts (event is federated via calendar_pg through the USL).
    for table, expected in expected_counts.items():
        try:
            got = _count(usl, table)
            status = "PASS" if got == expected else "FAIL"
            if status == "FAIL":
                overall_ok = False
            results.append((f"count.usl.{table}", f"{got} (want {expected})", status))
        except DataUnavailable as exc:
            overall_ok = False
            results.append((f"count.usl.{table}", str(exc)[:80], "FAIL"))

    duck = DuckDBBackend(
        settings.model_copy(
            update={"data_backend": DataBackend.DUCKDB, "sample_mode": True}
        )
    )

    for name, runner in _named_query_cases():
        try:
            usl_rows = runner(usl)
            tab_rows = runner(tables)
            duck_rows = runner(duck)
            match = (
                _rows_match(usl_rows, tab_rows)
                and _rows_match(tab_rows, duck_rows)
            )
            status = "PASS" if match else "FAIL"
            if not match:
                overall_ok = False
            detail = (
                f"usl={len(usl_rows)} tables={len(tab_rows)} duckdb={len(duck_rows)}"
            )
            if not match:
                detail += (
                    f" u={_preview(usl_rows)} t={_preview(tab_rows)} "
                    f"d={_preview(duck_rows)}"
                )
            results.append((f"query.{name}", detail, status))
        except Exception as exc:  # noqa: BLE001
            overall_ok = False
            results.append(
                (f"query.{name}", f"{type(exc).__name__}: {exc}"[:100], "FAIL")
            )

    _print_table(results)
    return 0 if overall_ok else 1


def _probe_auth(settings: Settings) -> tuple[str, ZetarisBackend]:
    """Try API key first, then login idToken. Never print secrets."""
    live = settings.model_copy(
        update={
            "sample_mode": False,
            "data_backend": DataBackend.ZETARIS,
            "zetaris_read_mode": ZetarisReadMode.TABLES,
        }
    )
    api_key = ""
    try:
        api_key = live.zetaris_api_key.get_secret_value().strip()
    except AttributeError:
        api_key = str(live.zetaris_api_key or "").strip()

    if api_key:
        try:
            key_only = live.model_copy(update={"zetaris_password": ""})
            key_backend = ZetarisBackend(key_only)
            key_backend.query("SELECT 1 AS n", source_tag="smoke.auth.key")
            if key_backend._auth_method == "api_key":
                print("Auth: API key")
                return "api_key", ZetarisBackend(live)
        except DataUnavailable:
            print("Auth: API key rejected; trying login…")

    login_backend = ZetarisBackend(live.model_copy(update={"zetaris_api_key": ""}))
    try:
        login_backend.query("SELECT 1 AS n", source_tag="smoke.auth.login")
        print("Auth: login idToken")
        return "login", login_backend
    except DataUnavailable as exc:
        print(f"Auth: FAIL ({exc})")
        return "FAIL", login_backend


def _count(backend: ZetarisBackend, table: str) -> int:
    rows = backend.query(
        f"SELECT COUNT(*) AS cnt FROM recall.{table}",
        source_tag=f"smoke.count.{table}",
    )
    if not rows:
        raise DataUnavailable(f"empty count for {table}")
    row = rows[0]
    for key in ("cnt", "COUNT(*)", "count(1)", "count"):
        if key in row and row[key] is not None:
            return int(str(row[key]))
    if len(row) == 1:
        return int(str(next(iter(row.values()))))
    raise DataUnavailable(f"cannot parse count row keys={list(row.keys())}")


def _named_query_cases() -> list[tuple[str, Callable[[Any], list[Any]]]]:
    def _handles(layer: Any) -> list[Any]:
        return find_handles_by_name("Karim", source_tag="smoke.handles", layer=layer)

    def _karim_handles(layer: Any) -> list[str]:
        rows = find_handles_by_name(
            "Karim Haddad", source_tag="smoke.kh_handles", layer=layer
        )
        return sorted({h.value for h in rows})

    def _interactions(layer: Any) -> list[Any]:
        values = _karim_handles(layer)
        return interactions_for_handles(
            values,
            source_tag="smoke.interactions",
            since_days=120,
            limit=60,
            now=SAMPLE_ANCHOR,
            layer=layer,
        )

    def _events(layer: Any) -> list[Any]:
        return upcoming_and_past_events(
            _KARIM_EMAILS,
            source_tag="smoke.events",
            days_back=180,
            days_forward=30,
            now=SAMPLE_ANCHOR,
            layer=layer,
        )

    def _cards(layer: Any) -> list[Any]:
        return contact_cards("Karim Haddad", source_tag="smoke.cards", layer=layer)

    def _all_h(layer: Any) -> list[Any]:
        return all_handles(source_tag="smoke.all_handles", layer=layer)

    def _all_c(layer: Any) -> list[Any]:
        return all_contacts(source_tag="smoke.all_contacts", layer=layer)

    def _all_i(layer: Any) -> list[Any]:
        return all_interactions(
            source_tag="smoke.all_interactions",
            since_days=400,
            limit=5000,
            now=SAMPLE_ANCHOR,
            layer=layer,
        )

    def _all_e(layer: Any) -> list[Any]:
        return all_events_in_window(
            source_tag="smoke.all_events",
            days_back=400,
            days_forward=60,
            now=SAMPLE_ANCHOR,
            layer=layer,
        )

    return [
        ("find_handles_by_name", _handles),
        ("interactions_for_handles", _interactions),
        ("upcoming_and_past_events", _events),
        ("contact_cards", _cards),
        ("all_handles", _all_h),
        ("all_contacts", _all_c),
        ("all_interactions", _all_i),
        ("all_events_in_window", _all_e),
    ]


def _rows_match(left: Sequence[Any], right: Sequence[Any]) -> bool:
    return _normalize(left) == _normalize(right)


def _normalize(rows: Sequence[Any]) -> list[tuple[tuple[str, str], ...]]:
    out: list[tuple[tuple[str, str], ...]] = []
    for row in rows:
        if hasattr(row, "model_dump"):
            data = row.model_dump()
        elif isinstance(row, dict):
            data = row
        else:
            data = {"value": row}
        items = tuple(sorted((str(k), _as_str(v)) for k, v in data.items()))
        out.append(items)
    out.sort()
    return out


def _as_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, dict)):
        text = json.dumps(value, sort_keys=True, default=str)
    else:
        text = str(value)
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _preview(rows: Sequence[Any], n: int = 1) -> str:
    if not rows:
        return "[]"
    return str(_normalize(rows[:n]))[:100]


def _print_table(rows: list[tuple[str, str, str]]) -> None:
    print()
    print(f"{'check':<36} {'detail':<52} {'result'}")
    print("-" * 100)
    for name, detail, status in rows:
        print(f"{name:<36} {detail[:52]:<52} {status}")
    print()
    failed = sum(1 for *_, s in rows if s == "FAIL")
    print(f"SUMMARY: {len(rows) - failed}/{len(rows)} PASS")


if __name__ == "__main__":
    raise SystemExit(main())
