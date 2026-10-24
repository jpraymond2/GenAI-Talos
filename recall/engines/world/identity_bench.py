"""Identity scenario test bench: load fixtures, ingest, score, compare."""

from __future__ import annotations

import csv
import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml

from recall.config import get_settings
from recall.data.duckdb_backend import DuckDBBackend
from recall.engines.world import world
from recall.engines.world.identity_scoring import load_scoring_config
from recall.engines.world.identity_service import IdentityService
from recall.engines.world.ingest import IngestPipelineService
from recall.engines.world.store import WorldStore
from recall.llm.generator import reset_client
from recall.tracing import Tracer

_ROOT = Path(__file__).resolve().parents[3]
_SCENARIOS = _ROOT / "tests" / "fixtures" / "identity_scenarios"
_SAMPLE = _ROOT / "data" / "sample"


@dataclass
class ScenarioResult:
    """One scenario outcome."""

    id: str
    expected_decision: str
    actual_decision: str
    expected_entity: str | None
    actual_entity: str | None
    probability: float
    passed: bool
    detail: str = ""
    flags: list[str] = field(default_factory=list)


def list_scenarios(path: Path | None = None) -> list[Path]:
    """YAML scenario files sorted by id."""
    root = path or _SCENARIOS
    return sorted(root.glob("*.yaml"))


def load_scenario(path: Path) -> dict[str, Any]:
    """Load one scenario YAML."""
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    data["_path"] = str(path)
    return data


def _copy_sample(dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for name in ("contacts.csv", "handles.csv", "interactions.csv", "events.csv"):
        shutil.copy2(_SAMPLE / name, dest / name)
    # Optional cache sidecar used by some readers.
    cache = _SAMPLE / "_text_cache.json"
    if cache.exists():
        shutil.copy2(cache, dest / "_text_cache.json")


def _append_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
    # Normalize list fields for contacts/events.
    normalized: list[dict[str, str]] = []
    for row in rows:
        out: dict[str, str] = {}
        for key in fieldnames:
            val = row.get(key, "")
            if isinstance(val, list):
                out[key] = json.dumps(val)
            elif val is None:
                out[key] = ""
            else:
                out[key] = str(val)
        normalized.append(out)
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, quoting=csv.QUOTE_MINIMAL)
        for row in normalized:
            writer.writerow(row)


def _apply_extra(sample_dir: Path, extra: dict[str, Any]) -> None:
    _append_csv(sample_dir / "contacts.csv", list(extra.get("contacts") or []))
    _append_csv(sample_dir / "handles.csv", list(extra.get("handles") or []))
    _append_csv(sample_dir / "interactions.csv", list(extra.get("interactions") or []))
    _append_csv(sample_dir / "events.csv", list(extra.get("events") or []))


def _entity_name(store: WorldStore, entity_id: str | None) -> str | None:
    if not entity_id:
        return None
    ent = store.get_entity(entity_id)
    return ent.canonical_name if ent else None


def _entity_id_by_name(store: WorldStore, name: str) -> str | None:
    needle = name.strip().lower()
    for ent in store.list_entities(type="person"):
        if ent.canonical_name.strip().lower() == needle:
            return ent.id
    return None


def _allowed_decisions(expected: dict[str, Any]) -> list[str]:
    raw = expected.get("decision")
    if isinstance(raw, list):
        return [str(x) for x in raw]
    return [str(raw)]


def _check_expected(
    scenario: dict[str, Any],
    *,
    decision: str,
    entity_name: str | None,
    probability: float,
    flags: list[str],
    store: WorldStore,
    top_entity_id: str | None,
) -> tuple[bool, str]:
    expected = scenario.get("expected") or {}
    allowed = _allowed_decisions(expected)
    if decision not in allowed:
        return False, f"decision {decision!r} not in {allowed}"

    never_dec = expected.get("never_decision")
    if never_dec and decision == never_dec:
        return False, f"decision must not be {never_dec}"

    exp_entity = expected.get("entity")
    if exp_entity is not None and str(exp_entity).lower() not in {"", "null", "none"}:
        if (entity_name or "").strip().lower() != str(exp_entity).strip().lower():
            return False, f"entity {entity_name!r} != {exp_entity!r}"

    never_entity = expected.get("never_entity")
    if never_entity and (entity_name or "").strip().lower() == str(never_entity).strip().lower():
        return False, f"must not link to {never_entity}"

    not_linked = expected.get("not_linked_entity")
    if not_linked:
        # Ensure the named person was not linked via this handle.
        handle = (scenario.get("handle") or {}).get("value", "")
        for row in store.list_identity_assumptions(handle_value=handle):
            if row.get("status") != "active":
                continue
            eid = row.get("candidate_entity_id")
            if not eid:
                continue
            name = _entity_name(store, str(eid))
            if name and name.strip().lower() == str(not_linked).strip().lower():
                if float(row.get("probability") or 0) >= 0.55 and row.get("decision") in {
                    "auto_linked",
                    "tentative",
                }:
                    return False, f"{not_linked} incorrectly linked"

    for flag in expected.get("flags") or []:
        if flag not in flags:
            return False, f"missing flag {flag}"

    min_p = expected.get("min_probability")
    if min_p is not None and probability < float(min_p):
        return False, f"probability {probability:.3f} < {min_p}"

    status_need = expected.get("entity_status")
    if status_need and top_entity_id:
        ent = store.get_entity(top_entity_id)
        st = (ent.attributes.get("status") if ent else "") or ""
        if st.lower() != str(status_need).lower():
            return False, f"entity status {st!r} != {status_need!r}"

    no_penalty = expected.get("no_penalty")
    if no_penalty and top_entity_id:
        handle = (scenario.get("handle") or {}).get("value", "")
        rows = [
            r
            for r in store.list_identity_assumptions(handle_value=handle)
            if r.get("score_file") and r.get("status") == "active"
        ]
        # Prefer the newest score file (bench rescore), not an earlier ingest pass.
        rows = sorted(rows, key=lambda r: str(r.get("decided_at") or ""), reverse=True)
        score_file = rows[0]["score_file"] if rows else ""
        if score_file and Path(score_file).exists():
            from recall.engines.world.identity_scoring import load_identity_score_record

            data = load_identity_score_record(score_file, handle_value=handle)
            for cand in data.get("candidates") or []:
                if cand.get("entity_id") != top_entity_id:
                    continue
                for pen in cand.get("penalties") or []:
                    if pen.get("name") == no_penalty:
                        return False, f"unexpected penalty {no_penalty}"

    return True, "ok"


def run_scenario(
    scenario: dict[str, Any],
    *,
    work_root: Path,
) -> ScenarioResult:
    """Run one scenario in an isolated sample+world copy."""
    import asyncio
    import os

    from recall.data.queries import all_handles, all_interactions
    from recall.data.query_layer import get_query_layer
    from recall.engines.world.anchors import load_anchors, normalize_email, normalize_phone
    from recall.engines.world.identity_scoring import score_handle

    sid = str(scenario.get("id") or "unknown")
    sample_dir = work_root / sid / "sample"
    db_path = work_root / sid / "world_sample.db"
    logs_dir = work_root / sid / "logs"
    _copy_sample(sample_dir)
    _apply_extra(sample_dir, scenario.get("extra") or {})

    os.environ["SAMPLE_MODE"] = "true"
    os.environ["NVIDIA_API_KEY"] = ""
    os.environ["SAMPLE_DATA_DIR"] = str(sample_dir.resolve())
    os.environ["LOGS_DIR"] = str(logs_dir.resolve())
    get_settings.cache_clear()
    reset_client()
    DuckDBBackend.clear_connection_cache()
    world.reset()

    settings = get_settings()
    object.__setattr__(settings, "sample_data_dir", sample_dir.resolve())
    object.__setattr__(settings, "sample_mode", True)
    object.__setattr__(settings, "logs_dir", logs_dir.resolve())

    tracer = Tracer(logs_dir=logs_dir)
    store: WorldStore | None = None
    try:
        with tracer.activate():
            store = WorldStore(path=db_path, settings=settings)
            layer = get_query_layer(settings, tracer=tracer)
            ingest = IngestPipelineService(store=store, settings=settings, tracer=tracer)
            # Sync context: ingest can asyncio.run identity scoring directly.
            ingest.run(layer)

            service = IdentityService(
                store=store, settings=settings, tracer=tracer, query_layer=layer
            )

            for emp in scenario.get("seed_employment") or []:
                eid = _entity_id_by_name(store, str(emp["entity_name"]))
                if not eid:
                    continue
                now = emp.get("updated_at") or datetime.now(timezone.utc).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                )
                store.upsert_employment_assumption(
                    {
                        "id": f"ea_bench_{sid}_{emp['company']}_{eid}",
                        "entity_id": eid,
                        "company": emp["company"],
                        "probability": 0.9,
                        "supporting_refs": [],
                        "contradicting_refs": [],
                        "scoring_version": 3,
                        "run_id": f"bench-{sid}",
                        "updated_at": now,
                        "status": "active",
                        "valid_from": emp.get("valid_from", ""),
                        "valid_to": emp.get("valid_to", ""),
                    }
                )

            for action in scenario.get("pre_actions") or []:
                if action.get("type") == "reject":
                    eid = _entity_id_by_name(store, str(action["entity_name"]))
                    if eid:
                        service.reject(str(action["handle"]), eid)

            handle = scenario.get("handle") or {}
            kind = str(handle.get("kind") or "email")
            value = str(handle.get("value") or "")
            display = ""
            body = ""
            for h in (scenario.get("extra") or {}).get("handles") or []:
                hv = str(h.get("value", ""))
                if hv.lower() == value.lower():
                    display = str(h.get("display_name") or display)
                elif kind == "phone":
                    digits = "".join(c for c in value if c.isdigit())
                    hv_digits = "".join(c for c in hv if c.isdigit())
                    if digits and hv_digits.endswith(digits[-10:]):
                        display = str(h.get("display_name") or display)
            email_ts = None
            for row in (scenario.get("extra") or {}).get("interactions") or []:
                hv = str(row.get("handle_value") or "")
                if kind == "email" and hv.lower() == value.lower():
                    body = str(row.get("text") or body)
                    try:
                        email_ts = datetime.fromisoformat(
                            str(row["ts_utc"]).replace("Z", "+00:00")
                        )
                    except (KeyError, ValueError):
                        pass
                if kind == "phone":
                    digits = "".join(c for c in value if c.isdigit())
                    hv_digits = "".join(c for c in hv if c.isdigit())
                    if digits and hv_digits.endswith(digits[-10:]):
                        body = str(row.get("text") or body)
                        try:
                            email_ts = datetime.fromisoformat(
                                str(row["ts_utc"]).replace("Z", "+00:00")
                            )
                        except (KeyError, ValueError):
                            pass

            anchors = load_anchors(settings=settings, layer=layer, tracer=tracer)
            handles = all_handles(source_tag="bench.handles", layer=layer)
            interactions = all_interactions(
                source_tag="bench.interactions",
                since_days=800,
                limit=5000,
                settings=settings,
                layer=layer,
            )
            anchor_names = {link.entity_name.strip().lower() for link in anchors}
            people = [
                p
                for p in store.list_entities(type="person")
                if p.canonical_name.strip().lower() in anchor_names
                or (p.attributes.get("status") or "").lower() == "new"
            ]
            phone_index, handles_by_entity = service._build_indexes(people, anchors, handles)
            name_to_id = {p.canonical_name.lower(): p.id for p in people}
            anchor_entity_ids = {
                name_to_id[link.entity_name.lower()]
                for link in anchors
                if link.entity_name.lower() in name_to_id
            }
            norm = (
                normalize_email(value)
                if kind == "email"
                else (normalize_phone(value) or value)
            )
            decision = asyncio.run(
                score_handle(
                    kind,
                    norm,
                    people=people,
                    interactions=interactions,
                    phone_index=phone_index,
                    handles_by_entity=handles_by_entity,
                    display_name=display,
                    body=body,
                    run_id=f"bench-{sid}-{uuid4().hex[:8]}",
                    settings=settings,
                    employment_rows=store.list_employment_assumptions(),
                    rejected_entity_ids=store.rejected_entity_ids_for_handle(norm),
                    email_ts=email_ts,
                    anchor_entity_ids=anchor_entity_ids,
                )
            )
            service.persist_decision(decision, anchors=anchors)

            actual_entity = _entity_name(store, decision.top_entity_id)
            passed, detail = _check_expected(
                scenario,
                decision=decision.decision,
                entity_name=actual_entity,
                probability=decision.top_probability,
                flags=list(decision.flags),
                store=store,
                top_entity_id=decision.top_entity_id,
            )
            exp_dec = _allowed_decisions(scenario.get("expected") or {})
            return ScenarioResult(
                id=sid,
                expected_decision="|".join(exp_dec),
                actual_decision=decision.decision,
                expected_entity=(scenario.get("expected") or {}).get("entity"),
                actual_entity=actual_entity,
                probability=decision.top_probability,
                passed=passed,
                detail=detail,
                flags=list(decision.flags),
            )
    finally:
        if store is not None:
            store.close()
        world.reset()
        DuckDBBackend.clear_connection_cache()
        get_settings.cache_clear()
        reset_client()


def run_bench(
    *,
    scenarios_dir: Path | None = None,
    work_root: Path | None = None,
) -> dict[str, Any]:
    """Run all scenarios; return summary + per-scenario results."""
    cfg = load_scoring_config()
    version = int(cfg.get("version", 3))
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    root = work_root or Path("logs") / "identity_bench_work" / ts
    root.mkdir(parents=True, exist_ok=True)

    results: list[ScenarioResult] = []
    for path in list_scenarios(scenarios_dir):
        scenario = load_scenario(path)
        results.append(run_scenario(scenario, work_root=root))

    passed = sum(1 for r in results if r.passed)
    total = len(results)
    payload = {
        "scoring_version": version,
        "timestamp": ts,
        "passed": passed,
        "total": total,
        "score": f"{passed}/{total} passed",
        "results": [
            {
                "id": r.id,
                "expected": r.expected_decision,
                "actual": r.actual_decision,
                "expected_entity": r.expected_entity,
                "actual_entity": r.actual_entity,
                "probability": r.probability,
                "pass": r.passed,
                "detail": r.detail,
                "flags": r.flags,
            }
            for r in results
        ],
    }
    out_dir = Path("logs") / "identity_bench"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{ts}.json"
    out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    payload["results_path"] = str(out_path)
    return payload


def format_bench_table(payload: dict[str, Any]) -> str:
    """Human-readable table for CLI."""
    lines = [
        f"{'id':6} {'expected':28} {'actual':16} {'prob':>7} {'pass':5}",
        "-" * 70,
    ]
    for row in payload.get("results") or []:
        mark = "PASS" if row.get("pass") else "FAIL"
        lines.append(
            f"{str(row.get('id')):6} "
            f"{str(row.get('expected'))[:28]:28} "
            f"{str(row.get('actual'))[:16]:16} "
            f"{float(row.get('probability') or 0):7.1%} "
            f"{mark:5}"
        )
    lines.append("-" * 70)
    lines.append(str(payload.get("score")))
    lines.append(f"scoring_version={payload.get('scoring_version')}  {payload.get('results_path')}")
    return "\n".join(lines)
