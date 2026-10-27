"""Phase 3: H-MEM notes, critic refetch, actions dry-run, Daniel Kim entity filter."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from recall.config import get_settings
from recall.data.duckdb_backend import DuckDBBackend
from recall.data.query_layer import clear_default_query_layer
from recall.data.queries import collect_entity_handle_values, _looks_like_address_handle
from recall.engines import hmem as hmem_mod
from recall.engines.hmem.store import hmem_db_path
from recall.engines.world import world as world_facade
from recall.llm.generator import reset_client
from recall.orchestrator import build_deps, mine_sample_notes, run


@pytest.fixture()
def phase3_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("SAMPLE_MODE", "true")
    monkeypatch.setenv("NVIDIA_API_KEY", "")
    monkeypatch.setenv("SAMPLE_DATA_DIR", "data/synthetic/tables")
    monkeypatch.setenv("CACHE_DIR", str(tmp_path / "empty_cache"))
    monkeypatch.setenv("LOGS_DIR", str(tmp_path / "logs"))
    get_settings.cache_clear()
    reset_client()
    clear_default_query_layer()
    DuckDBBackend.clear_connection_cache()
    world_facade.reset()
    hmem_mod.hmem.reset()
    # Allow notes.jsonl to mine into the dataset H-MEM (idempotent).
    settings = get_settings()
    hpath = hmem_db_path(settings)
    if hpath.is_file():
        from recall.engines.hmem.store import MemoryStoreService

        store = MemoryStoreService(path=hpath, settings=settings)
        store.set_meta("notes_mined", "")
        store.close()
    yield
    world_facade.reset()
    hmem_mod.hmem.reset()
    clear_default_query_layer()
    DuckDBBackend.clear_connection_cache()
    get_settings.cache_clear()
    reset_client()


def _deps(tmp_path: Path):
    return build_deps(sample_mode=True, logs_dir=tmp_path / "logs")


def test_notes_jsonl_mines_omar_and_mei_memories(phase3_env, tmp_path: Path) -> None:
    deps = _deps(tmp_path)
    deps.hmem_store.set_meta("notes_mined", "")
    asyncio.run(mine_sample_notes(deps))
    omar = next(
        e
        for e in deps.world_store.list_entities(type="person")
        if e.canonical_name == "Omar Haddad"
    )
    mei = next(
        e
        for e in deps.world_store.list_entities(type="person")
        if e.canonical_name == "Mei Tanaka"
    )
    assert deps.hmem_store.list_for_entity(omar.id)
    assert deps.hmem_store.list_for_entity(mei.id)


def test_rachel_brief_cites_omar_intro_note(phase3_env, tmp_path: Path) -> None:
    deps = _deps(tmp_path)
    deps.hmem_store.set_meta("notes_mined", "")
    asyncio.run(mine_sample_notes(deps))
    state = asyncio.run(run("I'm meeting Rachel Goldberg", deps=deps))
    assert state.final_brief is not None
    blob = " ".join(
        [state.final_brief.headline, *[p.text for p in state.final_brief.points]]
    ).lower()
    assert any(tok in blob for tok in ("intro", "omar", "noted", "haddad"))
    mem_ids = [eid for eid, e in state.evidence.items() if e.source == "memory"]
    assert mem_ids
    cited = {eid for p in state.final_brief.points for eid in p.evidence_ids}
    assert cited.intersection(mem_ids)


def test_refetch_event_on_mei_revision(phase3_env, tmp_path: Path) -> None:
    deps = _deps(tmp_path)
    state = asyncio.run(run("Sync with Mei tomorrow", deps=deps))
    assert state.round >= 2
    assert state.verdicts[-1].overall in {"pass", "unresolved"}
    trace_path = Path(deps.settings.logs_dir) / state.run_id / "trace.jsonl"
    text = trace_path.read_text(encoding="utf-8")
    assert '"refetch"' in text or "refetch sources=" in text
    # Generic rule — no planted dataset literals inside refetch payloads.
    for line in text.splitlines():
        if '"kind": "refetch"' not in line and '"kind":"refetch"' not in line:
            continue
        lowered = line.lower()
        assert "mei tanaka" not in lowered
        assert "financial model" not in lowered


def test_actions_dry_run_writes_file(phase3_env, tmp_path: Path) -> None:
    from datetime import datetime, timezone

    from recall.agents import actions
    from recall.schemas import ActionPlan, ActionStep, Brief, RunState, Utterance

    deps = _deps(tmp_path)
    run_id = "dryrun-test"
    logs = tmp_path / "logs"
    state = RunState(
        run_id=run_id,
        utterance=Utterance(text="hi", ts=datetime.now(timezone.utc), mode="text"),
        entity_name="Omar Haddad",
        final_brief=Brief(person="Omar Haddad", headline="test", points=[], caveats=[]),
        action_plan=ActionPlan(
            steps=[
                ActionStep(
                    tool="gmail.draft",
                    args={
                        "to_entity": "Omar Haddad",
                        "evidence_id": "e1",
                        "subject": "Re: follow-up",
                        "body_preview": "Hi Omar...",
                    },
                    summary="Draft a Gmail reply",
                ),
                ActionStep(
                    tool="calendar.hold",
                    args={"title": "Hold: Omar", "when": "2026-10-28T10:00:00Z"},
                    summary="Hold calendar time",
                ),
            ],
            spoken_summary="I can draft and hold.",
            requires_confirmation=True,
        ),
    )
    deps.settings = deps.settings.model_copy(
        update={"logs_dir": logs, "sample_mode": True}
    )
    result = asyncio.run(
        actions.execute(run_id=run_id, confirmed=True, deps=deps, state=state)
    )
    assert result["status"] == "dry_run"
    path = logs / run_id / "actions_dryrun.json"
    assert path.is_file()
    payload = json.loads(path.read_text(encoding="utf-8"))
    tools = {p["tool"] for p in payload["previews"]}
    assert "gmail.draft" in tools
    assert "calendar.hold" in tools


def test_actions_live_not_enabled(phase3_env, tmp_path: Path) -> None:
    from recall.agents import actions

    deps = _deps(tmp_path)
    deps.settings = deps.settings.model_copy(update={"sample_mode": False})
    result = asyncio.run(
        actions.execute(run_id="live-test", confirmed=True, deps=deps, state=None)
    )
    assert result["status"] == "not_enabled"


def test_actions_refused_without_confirmation(phase3_env, tmp_path: Path) -> None:
    from recall.agents import actions

    deps = _deps(tmp_path)
    result = asyncio.run(
        actions.execute(run_id="cancel-test", confirmed=False, deps=deps, state=None)
    )
    assert result["status"] == "cancelled"


def test_daniel_kim_investor_excludes_wedding_by_entity_id(
    phase3_env, tmp_path: Path
) -> None:
    """Evidence for Maple Ridge Daniel must never include UofT Dan's wedding."""
    deps = _deps(tmp_path)
    state = asyncio.run(run("I'm meeting Daniel Kim on Thursday", deps=deps))
    assert state.entity_name == "Daniel Kim"
    blob = " ".join(e.text.lower() for e in state.evidence.values())
    assert "wedding" not in blob
    assert "groomsman" not in blob
    entity = deps.world_store.get_entity(state.entity_id or "")
    assert entity is not None
    links = deps.world_store.list_handle_links(entity_id=entity.id)
    linked = [str(r.get("handle_value") or "") for r in links]
    handles = collect_entity_handle_values(
        list(entity.aliases), linked_handle_values=linked
    )
    assert not any(h.lower() == "daniel kim" for h in handles)
    assert not _looks_like_address_handle("Daniel Kim")
    assert _looks_like_address_handle("daniel@mapleridge.vc")
