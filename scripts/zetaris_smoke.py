"""Live smoke check: auth, counts, named-query parity (USL / tables / DuckDB).

Never prints tokens or passwords. Loads settings from .env via recall.config.
"""

from __future__ import annotations

import csv
import json
import re
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
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

_SAMPLE_TABLE_FILES = {
    "handles": "handles.csv",
    "interactions": "interactions.csv",
    "events": "events.csv",
    "contacts": "contacts.csv",
}

_PK_KEYS = ("contact_id", "event_id", "handle_id", "interaction_id")


@dataclass(frozen=True)
class ProbePerson:
    """Contact used for targeted smoke queries (resolved from SAMPLE_DATA_DIR)."""

    full_name: str
    emails: list[str] = field(default_factory=list)
    handle_values: list[str] = field(default_factory=list)


def _expected_counts_from_sample(sample_dir: Path) -> dict[str, int]:
    """Count data rows in settings.sample_data_dir CSVs (header excluded)."""
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
        _write_smoke_report(
            settings,
            results,
            [
                f"Expected counts from {sample_dir}: "
                + ", ".join(f"{k}={v}" for k, v in expected_counts.items()),
                "Auth failed — no live Zetaris session.",
            ],
        )
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

    try:
        probe = _pick_probe_person(sample_dir)
    except RuntimeError as exc:
        print(f"FAIL: {exc}")
        return 1
    print(
        f"Probe contact: {probe.full_name} "
        f"(emails={len(probe.emails)}, handles={len(probe.handle_values)})"
    )
    print()

    full_diff_lines: list[str] = []
    for name, runner in _named_query_cases(probe):
        try:
            usl_rows = runner(usl)
            tab_rows = runner(tables)
            duck_rows = runner(duck)
            empty_all = not usl_rows and not tab_rows and not duck_rows
            match = (
                (not empty_all)
                and _rows_match(usl_rows, tab_rows)
                and _rows_match(tab_rows, duck_rows)
            )
            status = "PASS" if match else "FAIL"
            if not match:
                overall_ok = False
            detail = (
                f"usl={len(usl_rows)} tables={len(tab_rows)} duckdb={len(duck_rows)}"
            )
            if empty_all:
                detail += " first_diff=0 rows on all backends"
                print(f"  DIFF query.{name}: 0 rows on all backends")
                full_diff_lines.append(f"query.{name}: 0 rows on all backends")
            elif not match:
                diff_block = _column_diff_block(
                    name, usl_rows, tab_rows, duck_rows
                )
                summary = diff_block[0] if diff_block else "unknown"
                detail += f" first_diff={summary}"
                print(f"  DIFF query.{name}:")
                for line in diff_block:
                    print(f"    {line}")
                full_diff_lines.append(f"query.{name}:")
                full_diff_lines.extend(f"  {line}" for line in diff_block)
            results.append((f"query.{name}", detail, status))
        except Exception as exc:  # noqa: BLE001
            overall_ok = False
            results.append(
                (f"query.{name}", f"{type(exc).__name__}: {exc}"[:100], "FAIL")
            )

    _print_table(results)
    _write_smoke_report(
        settings,
        results,
        [
            f"Expected counts from {sample_dir}: "
            + ", ".join(f"{k}={v}" for k, v in expected_counts.items()),
            f"Auth: {auth_label}",
            f"Probe: {probe.full_name}",
        ],
        full_diffs=full_diff_lines,
    )
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


def _csv_json_list(raw: str | None) -> list[str]:
    text = (raw or "").strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item).strip() for item in parsed if str(item).strip()]


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value)


def _pick_probe_person(sample_dir: Path) -> ProbePerson:
    """First contact with email(s), interaction handles, and calendar presence."""
    contacts = list(
        csv.DictReader((sample_dir / "contacts.csv").open(encoding="utf-8", newline=""))
    )
    handles = list(
        csv.DictReader((sample_dir / "handles.csv").open(encoding="utf-8", newline=""))
    )
    interactions = list(
        csv.DictReader(
            (sample_dir / "interactions.csv").open(encoding="utf-8", newline="")
        )
    )
    events = list(
        csv.DictReader((sample_dir / "events.csv").open(encoding="utf-8", newline=""))
    )
    ix_handles = {
        (row.get("handle_value") or "").strip()
        for row in interactions
        if (row.get("handle_value") or "").strip()
    }

    for contact in contacts:
        name = (contact.get("full_name") or "").strip()
        emails = _csv_json_list(contact.get("emails"))
        if not name or not emails:
            continue
        phones = _csv_json_list(contact.get("phones"))
        values: set[str] = set(emails) | set(phones)
        for handle in handles:
            hv = (handle.get("value") or "").strip()
            dn = (handle.get("display_name") or "").strip()
            kind = (handle.get("kind") or "").strip()
            if not hv:
                continue
            if hv in values or dn == name:
                values.add(hv)
                continue
            if kind == "wa_jid" and phones:
                local = hv.split("@", 1)[0]
                local_digits = _digits(local)
                if any(_digits(phone) == local_digits for phone in phones if phone):
                    values.add(hv)

        if not any(value in ix_handles for value in values):
            continue

        attendee_blob = "\n".join(row.get("attendees") or "" for row in events)
        if name not in attendee_blob and not any(
            email in attendee_blob for email in emails
        ):
            continue

        handle_values = sorted(values)
        return ProbePerson(
            full_name=name, emails=list(emails), handle_values=handle_values
        )

    raise RuntimeError(
        f"No probe contact in {sample_dir} with email + interactions + event attendees"
    )


def _named_query_cases(
    probe: ProbePerson,
) -> list[tuple[str, Callable[[Any], list[Any]]]]:
    def _handles(layer: Any) -> list[Any]:
        return find_handles_by_name(
            probe.full_name, source_tag="smoke.handles", layer=layer
        )

    def _interactions(layer: Any) -> list[Any]:
        return interactions_for_handles(
            probe.handle_values,
            source_tag="smoke.interactions",
            since_days=120,
            limit=60,
            now=SAMPLE_ANCHOR,
            layer=layer,
        )

    def _events(layer: Any) -> list[Any]:
        return upcoming_and_past_events(
            probe.emails,
            source_tag="smoke.events",
            days_back=180,
            days_forward=30,
            now=SAMPLE_ANCHOR,
            layer=layer,
        )

    def _cards(layer: Any) -> list[Any]:
        return contact_cards(probe.full_name, source_tag="smoke.cards", layer=layer)

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


def _row_dict(row: Any) -> dict[str, Any]:
    if hasattr(row, "model_dump"):
        return dict(row.model_dump())
    if isinstance(row, dict):
        return dict(row)
    return {"value": row}


def _row_pk(data: dict[str, Any]) -> str:
    for key in _PK_KEYS:
        if key in data:
            return str(data.get(key) or "")
    return ""


def _sorted_row_dicts(rows: Sequence[Any]) -> list[dict[str, Any]]:
    parsed = [_row_dict(row) for row in rows]
    parsed.sort(key=_row_pk)
    return parsed


def _rows_match(left: Sequence[Any], right: Sequence[Any]) -> bool:
    return _normalize(left) == _normalize(right)


def _normalize(rows: Sequence[Any]) -> list[tuple[tuple[str, str], ...]]:
    """Sort by primary key, then compare normalized cells (not full-tuple order)."""
    out: list[tuple[tuple[str, str], ...]] = []
    for data in _sorted_row_dicts(rows):
        items = tuple(
            sorted((str(k), _norm_cell(_as_str(v))) for k, v in data.items())
        )
        out.append(items)
    return out


def _as_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        cleaned = [str(item).strip() for item in value if str(item).strip()]
        if not cleaned:
            return ""
        return json.dumps(cleaned, ensure_ascii=False)
    if isinstance(value, dict):
        text = json.dumps(value, sort_keys=True, default=str)
    else:
        text = str(value)
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if text.startswith("["):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return text
        if isinstance(parsed, list):
            cleaned = [str(item).strip() for item in parsed if str(item).strip()]
            if not cleaned:
                return ""
            return json.dumps(cleaned, ensure_ascii=False)
    return text


def _norm_cell(text: str) -> str:
    """Strip, case-fold, and collapse empty / ``[]`` / null-like values."""
    trimmed = text.strip()
    if trimmed.lower() in {"", "none", "null", "[]"}:
        return ""
    return trimmed.casefold()


def _column_diff_block(
    name: str,
    usl_rows: Sequence[Any],
    tab_rows: Sequence[Any],
    duck_rows: Sequence[Any],
) -> list[str]:
    """Full column-by-column diff for the first mismatched pair (prefer USL vs DuckDB)."""
    pairs = (
        ("usl", usl_rows, "duckdb", duck_rows),
        ("usl", usl_rows, "tables", tab_rows),
        ("tables", tab_rows, "duckdb", duck_rows),
    )
    for left_label, left_rows, right_label, right_rows in pairs:
        left = _sorted_row_dicts(left_rows)
        right = _sorted_row_dicts(right_rows)
        if _normalize(left_rows) == _normalize(right_rows):
            continue
        if len(left) != len(right):
            return [
                f"len {left_label}={len(left)} {right_label}={len(right)}"
            ]
        for index, (row_l, row_r) in enumerate(zip(left, right, strict=True)):
            if _normalize([row_l]) == _normalize([row_r]):
                continue
            pk = _row_pk(row_l) or _row_pk(row_r) or str(index)
            lines = [
                f"row[{index}] pk={pk} ({left_label} vs {right_label})"
            ]
            keys = sorted(set(row_l) | set(row_r))
            for key in keys:
                lv_raw = row_l.get(key)
                rv_raw = row_r.get(key)
                lv = _norm_cell(_as_str(lv_raw))
                rv = _norm_cell(_as_str(rv_raw))
                if lv != rv:
                    lines.append(
                        f"{key}: {left_label}={lv_raw!r} {right_label}={rv_raw!r}"
                    )
            if len(lines) == 1:
                lines.append("(normalized cells equal after dump; raw keys differ)")
            return lines
    return ["unknown"]


def _print_table(rows: list[tuple[str, str, str]]) -> None:
    print()
    print(f"{'check':<36} {'detail':<52} {'result'}")
    print("-" * 100)
    for name, detail, status in rows:
        print(f"{name:<36} {detail[:52]:<52} {status}")
    print()
    failed = sum(1 for *_, s in rows if s == "FAIL")
    print(f"SUMMARY: {len(rows) - failed}/{len(rows)} PASS")


def _redact(text: str) -> str:
    """Strip tokens/passwords that must never land in committed smoke output."""
    out = text
    out = re.sub(r"(?i)(bearer\s+)\S+", r"\1***", out)
    out = re.sub(r"(?i)(api[_-]?key|token|password|secret)\s*[:=]\s*\S+", r"\1=***", out)
    out = re.sub(r"(?i)(Authorization:\s*)\S+", r"\1***", out)
    return out


def _write_smoke_report(
    settings: Settings,
    results: list[tuple[str, str, str]],
    lines: list[str],
    *,
    full_diffs: list[str] | None = None,
) -> Path:
    """Write redacted smoke transcript for judges (output_examples/zetaris_smoke.txt)."""
    dest = ROOT / "output_examples" / "zetaris_smoke.txt"
    dest.parent.mkdir(parents=True, exist_ok=True)
    failed = sum(1 for *_, s in results if s == "FAIL")
    parts = [
        "Zetaris smoke (secrets redacted)",
        f"REST={settings.zetaris_rest_url}",
        f"ORG={settings.zetaris_org_id} SCHEMA={settings.zetaris_schema}",
        f"SAMPLE_MODE={settings.sample_mode} DATA_BACKEND={settings.data_backend}",
        f"ZETARIS_READ_MODE={settings.zetaris_read_mode}",
        f"SAMPLE_DATA_DIR={settings.sample_data_dir}",
        "",
        *lines,
        "",
        f"{'check':<36} {'detail':<52} {'result'}",
        "-" * 100,
        *[
            f"{name:<36} {detail[:52]:<52} {status}"
            for name, detail, status in results
        ],
        "",
        f"SUMMARY: {len(results) - failed}/{len(results)} PASS",
    ]
    if full_diffs:
        parts.extend(["", "Full diffs (not truncated):", *full_diffs])
    parts.extend(
        [
            "",
            "Note: without a live cluster, SAMPLE_MODE traces may show",
            "fallback zetaris→duckdb reason=circuit_open — expected.",
        ]
    )
    body = "\n".join(parts)
    dest.write_text(_redact(body) + "\n", encoding="utf-8")
    print(f"Wrote {_redact(str(dest))}")
    return dest


if __name__ == "__main__":
    raise SystemExit(main())
