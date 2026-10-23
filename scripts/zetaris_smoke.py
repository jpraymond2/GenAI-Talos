"""Live smoke check: auth, row counts, named-query parity vs DuckDB.

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

from recall.config import DataBackend, Settings, get_settings  # noqa: E402
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

_EXPECTED_COUNTS = {
    "handles": 30,
    "interactions": 400,
    "events": 25,
    "contacts": 10,
}

_KARIM_EMAILS = ["karim@ledgerly.io", "alex.martin@mail.mcgill.ca"]


def main() -> int:
    get_settings.cache_clear()
    settings = get_settings()
    print("Zetaris smoke")
    print(f"  REST={settings.zetaris_rest_url}")
    print(f"  BASE={settings.zetaris_base_url}")
    print(f"  ORG={settings.zetaris_org_id} SCHEMA={settings.zetaris_schema}")
    print(f"  SAMPLE_MODE={settings.sample_mode} DATA_BACKEND={settings.data_backend}")
    print()

    results: list[tuple[str, str, str]] = []
    overall_ok = True

    auth_label, zetaris = _probe_auth(settings)
    results.append(("auth", auth_label, "PASS" if auth_label != "FAIL" else "FAIL"))
    if auth_label == "FAIL":
        _print_table(results)
        return 1

    count_ok = True
    for table, expected in _EXPECTED_COUNTS.items():
        try:
            got = _count(zetaris, table)
            status = "PASS" if got == expected else "FAIL"
            if status == "FAIL":
                count_ok = False
                overall_ok = False
            results.append((f"count.{table}", f"{got} (want {expected})", status))
            if table == "interactions" and got != expected:
                print(
                    f"NOTE: interactions count is {got}, not {expected}. "
                    "CSV may need different multiLine/escape options — recreate the table."
                )
        except DataUnavailable as exc:
            count_ok = False
            overall_ok = False
            results.append((f"count.{table}", str(exc)[:80], "FAIL"))

    duck_settings = settings.model_copy(
        update={"data_backend": DataBackend.DUCKDB, "sample_mode": True}
    )
    duck = DuckDBBackend(duck_settings)

    for name, runner in _named_query_cases():
        try:
            z_rows = runner(zetaris)
            d_rows = runner(duck)
            match = _rows_match(z_rows, d_rows)
            status = "PASS" if match else "FAIL"
            if not match:
                overall_ok = False
            detail = f"zetaris={len(z_rows)} duckdb={len(d_rows)}"
            if not match:
                detail += f" sample_z={_preview(z_rows)} sample_d={_preview(d_rows)}"
            results.append((f"query.{name}", detail, status))
        except Exception as exc:  # noqa: BLE001
            overall_ok = False
            results.append((f"query.{name}", f"{type(exc).__name__}: {exc}"[:100], "FAIL"))

    _print_table(results)
    if not count_ok:
        print("Row-count check incomplete or mismatched.")
    return 0 if overall_ok else 1


def _probe_auth(settings: Settings) -> tuple[str, ZetarisBackend]:
    """Try API key first, then login idToken. Never print secrets."""
    live = settings.model_copy(
        update={"sample_mode": False, "data_backend": DataBackend.ZETARIS}
    )
    api_key = live.zetaris_api_key.get_secret_value().strip()
    if api_key:
        key_backend = ZetarisBackend(live)
        try:
            # Force key path: empty password so Not-Allowed cannot silently login mid-probe.
            key_only = live.model_copy(update={"zetaris_password": ""})
            key_backend = ZetarisBackend(key_only)
            key_backend.query("SELECT 1 AS n", source_tag="smoke.auth.key")
            if key_backend._auth_method == "api_key":
                print("Auth: API key")
                return "api_key", ZetarisBackend(live)
        except DataUnavailable:
            print("Auth: API key rejected; trying login…")

    login_backend = ZetarisBackend(
        live.model_copy(update={"zetaris_api_key": ""})
    )
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
    # Column name may be cnt, COUNT(*), count(1), etc.
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
    # DuckDB on Windows CSVs keeps CRLF; Zetaris/Spark normalizes to LF.
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _preview(rows: Sequence[Any], n: int = 1) -> str:
    if not rows:
        return "[]"
    sample = _normalize(rows[:n])
    text = str(sample)
    return text[:120]


def _print_table(rows: list[tuple[str, str, str]]) -> None:
    print()
    print(f"{'check':<32} {'detail':<48} {'result'}")
    print("-" * 96)
    for name, detail, status in rows:
        print(f"{name:<32} {detail[:48]:<48} {status}")
    print()
    failed = sum(1 for *_, s in rows if s == "FAIL")
    print(f"SUMMARY: {len(rows) - failed}/{len(rows)} PASS")


if __name__ == "__main__":
    raise SystemExit(main())
