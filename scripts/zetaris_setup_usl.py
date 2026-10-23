"""Create / activate the Recall USL + DQ rules on live Zetaris.

Uses ZetarisBackend auth (API key, then login idToken). Never prints tokens
or passwords. Each Lightning statement is ONE REST request.

Usage:
  uv run python scripts/zetaris_setup_usl.py
  uv run python scripts/zetaris_setup_usl.py --reset
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from recall.config import DataBackend, get_settings  # noqa: E402
from recall.data.query_layer import DataUnavailable  # noqa: E402
from recall.data.zetaris_backend import ZetarisBackend  # noqa: E402
from recall.data.zetaris_usl import (  # noqa: E402
    DEFAULT_USL_NAMESPACE,
    USL_COLUMN_RENAMES,
)

# Namespace + model name; leaf tables live under DEFAULT_USL_NAMESPACE.
_NS = "lightning.metastore.recallai"
_USL = "recall"
_USL_PATH = DEFAULT_USL_NAMESPACE  # lightning.metastore.recallai.recall
assert _USL_PATH == f"{_NS}.{_USL}"
# Deployed rename list (org→organisation, …) — single source: zetaris_usl.py
assert USL_COLUMN_RENAMES["contact"]["org"] == "organisation"
_ADMIN_TIMEOUT = 120.0

_EXPECTED = {
    "contact": 10,
    "handle": 30,
    "interaction": 400,
    "event": 25,
}


@dataclass(frozen=True)
class UslVariant:
    """One COMPILE USL attempt (and the matching activate / DQ / count paths)."""

    name: str
    compile_sql: str
    # logical_name -> (fully_qualified_usl_table, activate_select)
    tables: dict[str, tuple[str, str]]
    dq: list[tuple[str, str, str]]  # (rule_name, usl_table, predicate)


def main() -> int:
    parser = argparse.ArgumentParser(description="Set up Recall USL on Zetaris")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="REMOVE USL recall before compiling",
    )
    args = parser.parse_args()

    get_settings.cache_clear()
    settings = get_settings()
    live = settings.model_copy(
        update={"sample_mode": False, "data_backend": DataBackend.ZETARIS}
    )
    backend = ZetarisBackend(live)

    print("Zetaris USL setup")
    print(f"  REST={live.zetaris_rest_url}")
    print(f"  ORG={live.zetaris_org_id}  reset={args.reset}")
    print()

    # Force auth once (prefer login when no key — same path as smoke).
    try:
        _exec(backend, "SELECT 1 AS n", "auth.ping")
        print(f"Auth: {backend._auth_method or 'ok'}")
    except DataUnavailable as exc:
        print(f"Auth FAILED: {exc}")
        return 1
    print()

    try:
        _exec(
            backend,
            "CREATE NAMESPACE IF NOT EXISTS lightning.metastore",
            "ns.metastore",
        )
        _exec(
            backend,
            "CREATE NAMESPACE IF NOT EXISTS lightning.metastore.recallai",
            "ns.recallai",
        )
    except DataUnavailable as exc:
        print(f"STOP: namespace step failed\n{exc}")
        return 1

    if args.reset:
        try:
            _exec(
                backend,
                f"REMOVE USL {_USL} NAMESPACE {_NS}",
                "usl.remove",
            )
        except DataUnavailable as exc:
            print(f"(REMOVE USL note — continuing): {exc}")
            print()

    variant: UslVariant | None = None
    if not args.reset and _usl_ready(backend):
        print("USL already present — skipping COMPILE (use --reset to rebuild)")
        print()
        variant = _default_variant()
    else:
        variant = _compile_with_fallbacks(backend)
        if variant is None:
            return 1
        print(f"COMPILE variant that worked: {variant.name}")
        print()

    assert variant is not None
    try:
        for logical, (fq_table, select_sql) in variant.tables.items():
            stmt = f"ACTIVATE USL TABLE {fq_table} AS {select_sql}"
            _exec(backend, stmt, f"activate.{logical}")

        for rule_name, fq_table, predicate in variant.dq:
            stmt = f"REGISTER DQ {rule_name} TABLE {fq_table} AS {predicate}"
            _exec(backend, stmt, f"dq.register.{rule_name}")

        for rule_name, fq_table, _predicate in variant.dq:
            _run_dq(backend, rule_name, fq_table)

        print("USL row counts:")
        all_ok = True
        for logical, (fq_table, _) in variant.tables.items():
            expected = _EXPECTED[logical]
            count = _count(backend, fq_table)
            status = "PASS" if count == expected else "FAIL"
            if status == "FAIL":
                all_ok = False
            print(f"  {logical:<12} {count:>5} (want {expected})  {status}")
        print()
        if not all_ok:
            print("COUNT check FAILED")
            return 1
        print("USL setup complete.")
        return 0
    except DataUnavailable as exc:
        print(f"STOP:\n{exc}")
        return 1


def _default_variant() -> UslVariant:
    return _variants()[0]


def _variants() -> list[UslVariant]:
    """COMPILE attempts in the order requested by the user."""
    # --- (baseline) multi-table, original column names ---
    baseline_ddl = """\
CREATE TABLE contact (contact_id varchar(64) NOT NULL PRIMARY KEY, full_name varchar(200), org varchar(200), title varchar(200), city varchar(100), emails varchar(1000), phones varchar(1000), source varchar(50));
CREATE TABLE handle (handle_id varchar(64) NOT NULL PRIMARY KEY, kind varchar(20), value varchar(200), display_name varchar(200), source varchar(50));
CREATE TABLE interaction (interaction_id varchar(64) NOT NULL PRIMARY KEY, source varchar(20), thread_id varchar(100), handle_value varchar(200), direction varchar(5), ts_utc varchar(40), subject varchar(500), text varchar(4000), source_ref varchar(100));
CREATE TABLE event (event_id varchar(64) NOT NULL PRIMARY KEY, title varchar(300), start_utc varchar(40), end_utc varchar(40), location varchar(300), attendees varchar(2000), source_ref varchar(100))"""

    baseline_tables = {
        "contact": (
            f"{_USL_PATH}.contact",
            "SELECT COALESCE(contact_id,'') AS contact_id, full_name, org, title, city, "
            "emails, phones, source FROM recall.contacts",
        ),
        "handle": (
            f"{_USL_PATH}.handle",
            "SELECT COALESCE(handle_id,'') AS handle_id, kind, value, display_name, "
            "source FROM recall.handles",
        ),
        "interaction": (
            f"{_USL_PATH}.interaction",
            "SELECT COALESCE(interaction_id,'') AS interaction_id, source, thread_id, "
            "handle_value, direction, ts_utc, subject, text, source_ref "
            "FROM recall.interactions",
        ),
        "event": (
            f"{_USL_PATH}.event",
            "SELECT COALESCE(event_id,'') AS event_id, title, start_utc, end_utc, "
            "location, attendees, source_ref FROM calendar_pg.calendar_events",
        ),
    }
    baseline_dq = [
        (
            "valid_handle_kind",
            f"{_USL_PATH}.handle",
            "kind IN ('email', 'phone', 'wa_jid', 'imessage')",
        ),
        (
            "has_source_ref",
            f"{_USL_PATH}.interaction",
            "source_ref IS NOT NULL AND source_ref LIKE '%:%'",
        ),
    ]

    # --- (a) rename possibly-reserved columns ---
    renamed_ddl = """\
CREATE TABLE contact (contact_id varchar(64) NOT NULL PRIMARY KEY, full_name varchar(200), organisation varchar(200), title varchar(200), city varchar(100), emails varchar(1000), phones varchar(1000), source_system varchar(50));
CREATE TABLE handle (handle_id varchar(64) NOT NULL PRIMARY KEY, kind varchar(20), handle_value varchar(200), display_name varchar(200), source_system varchar(50));
CREATE TABLE interaction (interaction_id varchar(64) NOT NULL PRIMARY KEY, source_system varchar(20), thread_id varchar(100), handle_value varchar(200), direction varchar(5), ts_utc varchar(40), subject varchar(500), body varchar(4000), source_ref varchar(100));
CREATE TABLE event (event_id varchar(64) NOT NULL PRIMARY KEY, title varchar(300), start_utc varchar(40), end_utc varchar(40), place varchar(300), attendees varchar(2000), source_ref varchar(100))"""

    renamed_tables = {
        "contact": (
            f"{_USL_PATH}.contact",
            "SELECT COALESCE(contact_id,'') AS contact_id, full_name, "
            "org AS organisation, title, city, emails, phones, "
            "source AS source_system FROM recall.contacts",
        ),
        "handle": (
            f"{_USL_PATH}.handle",
            "SELECT COALESCE(handle_id,'') AS handle_id, kind, "
            "value AS handle_value, display_name, "
            "source AS source_system FROM recall.handles",
        ),
        "interaction": (
            f"{_USL_PATH}.interaction",
            "SELECT COALESCE(interaction_id,'') AS interaction_id, "
            "source AS source_system, thread_id, handle_value, direction, "
            "ts_utc, subject, text AS body, source_ref FROM recall.interactions",
        ),
        "event": (
            f"{_USL_PATH}.event",
            "SELECT COALESCE(event_id,'') AS event_id, title, start_utc, end_utc, "
            "location AS place, attendees, source_ref FROM calendar_pg.calendar_events",
        ),
    }

    # --- (b) renamed + each column on its own line ---
    multiline_ddl = """\
CREATE TABLE contact (
  contact_id varchar(64) NOT NULL PRIMARY KEY,
  full_name varchar(200),
  organisation varchar(200),
  title varchar(200),
  city varchar(100),
  emails varchar(1000),
  phones varchar(1000),
  source_system varchar(50)
);
CREATE TABLE handle (
  handle_id varchar(64) NOT NULL PRIMARY KEY,
  kind varchar(20),
  handle_value varchar(200),
  display_name varchar(200),
  source_system varchar(50)
);
CREATE TABLE interaction (
  interaction_id varchar(64) NOT NULL PRIMARY KEY,
  source_system varchar(20),
  thread_id varchar(100),
  handle_value varchar(200),
  direction varchar(5),
  ts_utc varchar(40),
  subject varchar(500),
  body varchar(4000),
  source_ref varchar(100)
);
CREATE TABLE event (
  event_id varchar(64) NOT NULL PRIMARY KEY,
  title varchar(300),
  start_utc varchar(40),
  end_utc varchar(40),
  place varchar(300),
  attendees varchar(2000),
  source_ref varchar(100)
)"""

    variants = [
        UslVariant(
            name="baseline_multi_table",
            compile_sql=_compile_block(_USL, baseline_ddl),
            tables=baseline_tables,
            dq=baseline_dq,
        ),
        UslVariant(
            name="a_renamed_columns",
            compile_sql=_compile_block(_USL, renamed_ddl),
            tables=renamed_tables,
            dq=baseline_dq,  # predicates use kind / source_ref only
        ),
        UslVariant(
            name="b_renamed_multiline",
            compile_sql=_compile_block(_USL, multiline_ddl),
            tables=renamed_tables,
            dq=baseline_dq,
        ),
    ]

    # --- (c) one table per USL ---
    per_table: list[tuple[str, str, str, str]] = [
        (
            "contact",
            "recall_contact",
            """CREATE TABLE contact (
  contact_id varchar(64) NOT NULL PRIMARY KEY,
  full_name varchar(200),
  organisation varchar(200),
  title varchar(200),
  city varchar(100),
  emails varchar(1000),
  phones varchar(1000),
  source_system varchar(50)
)""",
            renamed_tables["contact"][1],
        ),
        (
            "handle",
            "recall_handle",
            """CREATE TABLE handle (
  handle_id varchar(64) NOT NULL PRIMARY KEY,
  kind varchar(20),
  handle_value varchar(200),
  display_name varchar(200),
  source_system varchar(50)
)""",
            renamed_tables["handle"][1],
        ),
        (
            "interaction",
            "recall_interaction",
            """CREATE TABLE interaction (
  interaction_id varchar(64) NOT NULL PRIMARY KEY,
  source_system varchar(20),
  thread_id varchar(100),
  handle_value varchar(200),
  direction varchar(5),
  ts_utc varchar(40),
  subject varchar(500),
  body varchar(4000),
  source_ref varchar(100)
)""",
            renamed_tables["interaction"][1],
        ),
        (
            "event",
            "recall_event",
            """CREATE TABLE event (
  event_id varchar(64) NOT NULL PRIMARY KEY,
  title varchar(300),
  start_utc varchar(40),
  end_utc varchar(40),
  place varchar(300),
  attendees varchar(2000),
  source_ref varchar(100)
)""",
            renamed_tables["event"][1],
        ),
    ]

    # Variant (c) is special: compile is a sequence; we fold it into one UslVariant
    # whose compile_sql is a marker handled in _compile_with_fallbacks.
    c_tables = {
        logical: (f"{_NS}.{usl_name}.{logical}", select_sql)
        for logical, usl_name, _ddl, select_sql in per_table
    }
    c_dq = [
        (
            "valid_handle_kind",
            f"{_NS}.recall_handle.handle",
            "kind IN ('email', 'phone', 'wa_jid', 'imessage')",
        ),
        (
            "has_source_ref",
            f"{_NS}.recall_interaction.interaction",
            "source_ref IS NOT NULL AND source_ref LIKE '%:%'",
        ),
    ]
    # Encode per-table compile payloads as JSON in compile_sql for the runner.
    c_payload = json.dumps(
        [
            {"usl": usl_name, "ddl": ddl}
            for _logical, usl_name, ddl, _sel in per_table
        ]
    )
    variants.append(
        UslVariant(
            name="c_one_table_per_usl",
            compile_sql=f"__PER_TABLE__{c_payload}",
            tables=c_tables,
            dq=c_dq,
        )
    )
    return variants


def _compile_block(usl_name: str, ddl: str) -> str:
    return (
        f"COMPILE USL {usl_name} DEPLOY NAMESPACE {_NS} DDL\n{ddl.strip()}"
    )


def _compile_with_fallbacks(backend: ZetarisBackend) -> UslVariant | None:
    last_error = ""
    for variant in _variants():
        print(f"--- COMPILE attempt: {variant.name} ---")
        try:
            if variant.compile_sql.startswith("__PER_TABLE__"):
                items = json.loads(variant.compile_sql.removeprefix("__PER_TABLE__"))
                for item in items:
                    sql = _compile_block(item["usl"], item["ddl"])
                    _exec(backend, sql, f"compile.{item['usl']}")
            else:
                _exec(backend, variant.compile_sql, f"compile.{variant.name}")
            print(f"COMPILE OK with variant: {variant.name}")
            print()
            return variant
        except DataUnavailable as exc:
            last_error = str(exc)
            print("COMPILE FAILED — full server error:")
            print(last_error)
            print()
            continue
    print("All COMPILE variants failed.")
    print(last_error)
    return None


def _usl_ready(backend: ZetarisBackend) -> bool:
    try:
        _count(backend, f"{_USL_PATH}.contact")
        return True
    except DataUnavailable:
        return False


def _exec(backend: ZetarisBackend, sql: str, tag: str) -> dict[str, Any]:
    preview = " ".join(sql.split())
    if len(preview) > 100:
        preview = preview[:97] + "..."
    print(f">>> [{tag}] {preview}")
    try:
        payload = backend.run_raw(sql, source_tag=f"usl.{tag}", timeout=_ADMIN_TIMEOUT)
    except DataUnavailable:
        raise
    print(_safe_dump(payload))
    print()
    return payload


def _run_dq(backend: ZetarisBackend, rule_name: str, fq_table: str) -> None:
    # Try a few RUN DQ spellings; stop at the first that works.
    candidates = [
        f"RUN DQ {rule_name} TABLE {fq_table}",
        f"RUN DQ {rule_name} ON {fq_table}",
        f"RUN DQ {rule_name}",
    ]
    last: DataUnavailable | None = None
    for stmt in candidates:
        try:
            payload = _exec(backend, stmt, f"dq.run.{rule_name}")
            total, valid, invalid = _parse_dq_stats(payload)
            print(
                f"DQ {rule_name}: total={total} valid={valid} invalid={invalid}"
            )
            print()
            return
        except DataUnavailable as exc:
            last = exc
            print(f"(RUN DQ spelling failed, trying next): {exc}")
            print()
    raise DataUnavailable(str(last) if last else f"RUN DQ {rule_name} failed")


def _parse_dq_stats(payload: dict[str, Any]) -> tuple[str, str, str]:
    rows: list[dict[str, Any]] = []
    try:
        rows = ZetarisBackend._extract_rows(payload)
    except DataUnavailable:
        pass
    if not rows:
        return ("?", "?", "?")
    row = rows[0]
    lower = {str(k).lower().replace(" ", "_"): v for k, v in row.items()}

    def pick(*names: str) -> str | None:
        for name in names:
            if name in lower and lower[name] is not None:
                return str(lower[name])
        return None

    total = pick("total_record", "total", "totalcount", "count", "cnt")
    valid = pick("valid_record", "valid", "validcount", "pass", "passed")
    invalid = pick("invalid_record", "invalid", "invalidcount", "fail", "failed")
    if total is not None and valid is not None and invalid is not None:
        return total, valid, invalid
    # Positional fallback when headers are unknown but values are ordered.
    vals = [str(v) for v in row.values()]
    if len(vals) >= 6:
        return vals[3], vals[4], vals[5]
    if len(vals) >= 3:
        return vals[-3], vals[-2], vals[-1]
    return ("?", "?", "?")


def _count(backend: ZetarisBackend, fq_table: str) -> int:
    payload = backend.run_raw(
        f"SELECT COUNT(*) AS cnt FROM {fq_table}",
        source_tag="usl.count",
        timeout=_ADMIN_TIMEOUT,
    )
    rows = ZetarisBackend._extract_rows(payload)
    if not rows:
        raise DataUnavailable(f"empty count for {fq_table}")
    row = rows[0]
    for key in ("cnt", "COUNT(*)", "count(1)", "count"):
        if key in row and row[key] is not None:
            return int(str(row[key]))
    return int(str(next(iter(row.values()))))


def _safe_dump(payload: Any) -> str:
    """Pretty-print response JSON with any token-like keys redacted."""
    redacted = _redact(payload)
    try:
        text = json.dumps(redacted, indent=2, default=str)
    except TypeError:
        text = str(redacted)
    if len(text) > 4000:
        return text[:4000] + "\n… (truncated)"
    return text


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            key_l = str(key).lower()
            if any(
                part in key_l
                for part in ("token", "password", "authorization", "secret", "apikey")
            ):
                out[key] = "***"
            else:
                out[key] = _redact(item)
        return out
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


if __name__ == "__main__":
    raise SystemExit(main())
