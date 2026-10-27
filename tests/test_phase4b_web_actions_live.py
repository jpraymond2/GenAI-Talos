"""Phase 4b: web scout on retrieve, actions quality, LLM_LIVE fallback."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from recall.config import get_settings
from recall.data.duckdb_backend import DuckDBBackend
from recall.data.query_layer import clear_default_query_layer
from recall.engines import hmem as hmem_mod
from recall.engines.world import world as world_facade
from recall.llm.generator import SampleCacheMiss, generate, reset_client, use_sample_provider
from recall.orchestrator import build_deps, run
from recall.schemas import Brief, Evidence, RunState, TalkingPoint, Utterance


@pytest.fixture()
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("SAMPLE_MODE", "true")
    monkeypatch.setenv("LLM_LIVE", "false")
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
    yield
    world_facade.reset()
    hmem_mod.hmem.reset()
    clear_default_query_layer()
    DuckDBBackend.clear_connection_cache()
    get_settings.cache_clear()
    reset_client()


def test_omar_brief_includes_payloop_web(env, tmp_path: Path) -> None:
    deps = build_deps(
        sample_mode=True,
        logs_dir=tmp_path / "logs",
        hmem_path=tmp_path / "hmem_omar.db",
    )
    state = asyncio.run(run("I'm meeting Omar Haddad on Wednesday", deps=deps))
    assert state.final_brief is not None
    blob = " ".join(p.text for p in state.final_brief.points).lower()
    assert "payloop" in blob
    assert any(e.source == "web" and e.url for e in state.evidence.values())
    trace = (tmp_path / "logs" / state.run_id / "trace.jsonl").read_text()
    assert "web_scout sample hits=" in trace
    assert "org=Payloop" in trace or "Payloop" in trace


def test_memory_point_strips_note_that(env, tmp_path: Path) -> None:
    from recall.agents.offline import _select_points

    item = Evidence(
        id="m1",
        source="memory",
        source_ref="mem_x",
        ts=datetime(2026, 10, 20, tzinfo=timezone.utc),
        text="Note that Omar Haddad wants an intro to Rachel Goldberg by end of month.",
        tags=["memory"],
    )
    points = _select_points({"m1": item}, person="Rachel Goldberg", max_points=3)
    assert points
    assert "Note that" not in points[0].text
    assert "Omar Haddad wants an intro" in points[0].text


def test_intro_action_is_double_opt_in(env) -> None:
    from recall.agents import actions

    pack = {
        "e2": Evidence(
            id="e2",
            source="gmail",
            source_ref="g1",
            ts=datetime(2026, 9, 30, tzinfo=timezone.utc),
            text="could you intro me to Rachel Goldberg at Northwind?",
            tags=["open_loop"],
        )
    }
    brief = Brief(
        person="Omar Haddad",
        headline="Omar",
        points=[
            TalkingPoint(
                text="Omar asked for an intro to Rachel.",
                evidence_ids=["e2"],
                kind="open_loop",
                priority=1,
            )
        ],
    )
    state = RunState(
        run_id="act-intro",
        utterance=Utterance(text="meet Omar", ts=datetime.now(timezone.utc), mode="text"),
        entity_name="Omar Haddad",
        evidence=pack,
        final_brief=brief,
    )
    plan = asyncio.run(actions.plan(state))
    assert plan.steps
    step = plan.steps[0]
    assert step.tool == "gmail.draft"
    assert step.args.get("to") == "Rachel Goldberg"
    assert step.args.get("cc") == "Omar Haddad"
    body = step.args.get("body_preview") or ""
    assert "could you intro me" not in body.lower()
    assert "Rachel" in body or "open to" in body.lower()


def test_restaurant_skipped_when_location_present(env) -> None:
    from recall.agents import actions

    pack = {
        "e1": Evidence(
            id="e1",
            source="calendar",
            source_ref="c1",
            ts=datetime(2026, 10, 28, 14, 0, tzinfo=timezone.utc),
            text="Coffee w/ Omar Haddad (Café Névé, Montréal)",
            tags=["upcoming"],
        )
    }
    state = RunState(
        run_id="act-meal",
        utterance=Utterance(text="coffee with Omar", ts=datetime.now(timezone.utc), mode="text"),
        entity_name="Omar Haddad",
        evidence=pack,
        context={"meal_hook": True, "suggested_place": "somewhere"},
    )
    plan = asyncio.run(actions.plan(state))
    assert all(s.tool != "suggester.restaurant" for s in plan.steps)


def test_llm_live_falls_back_on_nvidia_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cache = tmp_path / "cache" / "llm"
    cache.mkdir(parents=True)
    (cache / "default.json").write_text(
        json.dumps({"default": "cached reply from fixture"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("SAMPLE_MODE", "true")
    monkeypatch.setenv("LLM_LIVE", "true")
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test-key-not-real")
    monkeypatch.setenv("CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("LOGS_DIR", str(tmp_path / "logs"))
    get_settings.cache_clear()
    reset_client()
    assert use_sample_provider() is False

    async def _boom(*_a, **_k):
        raise TimeoutError("simulated nvidia timeout")

    monkeypatch.setattr("recall.llm.generator._nvidia_chat", _boom)
    text = asyncio.run(generate("anything", role="default"))
    assert text == "cached reply from fixture"

    # Empty role cache → SampleCacheMiss (agent then uses offline_rules).
    (cache / "writer.json").write_text("{}", encoding="utf-8")
    with pytest.raises(SampleCacheMiss):
        asyncio.run(generate("no cache key for this", role="writer"))
