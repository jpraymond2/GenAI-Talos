"""Offline-path correctness on the synthetic Jordan Lefebvre world."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from recall.config import get_settings
from recall.data.duckdb_backend import DuckDBBackend
from recall.data.query_layer import clear_default_query_layer
from recall.engines.world import world as world_facade
from recall.llm.generator import reset_client
from recall.orchestrator import build_deps, run


@pytest.fixture()
def synthetic_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Force synthetic tables + empty LLM cache (offline_rules path)."""
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
    yield
    world_facade.reset()
    clear_default_query_layer()
    DuckDBBackend.clear_connection_cache()
    get_settings.cache_clear()
    reset_client()


def _deps(tmp_path: Path):
    return build_deps(sample_mode=True, logs_dir=tmp_path / "logs")


def test_omar_clarifies_three(synthetic_env, tmp_path: Path) -> None:
    state = asyncio.run(run("I'm meeting Omar", deps=_deps(tmp_path)))
    assert state.status == "needs_clarification"
    names = {c.name for c in state.candidates}
    assert any("Haddad" in n for n in names)
    assert any("MBA" in n or "Benali" in n for n in names)
    assert any("Kassem" in n or "proprio" in n.lower() for n in names)


def test_omar_haddad_wednesday_brief(synthetic_env, tmp_path: Path) -> None:
    state = asyncio.run(
        run("I'm meeting Omar Haddad on Wednesday", deps=_deps(tmp_path))
    )
    assert state.status == "done"
    assert state.final_brief is not None
    assert "Omar Haddad" in (state.entity_name or "")
    assert "Omar Haddad" in state.final_brief.person
    blob = state.final_brief.headline + " " + " ".join(p.text for p in state.final_brief.points)
    assert "Rachel" in blob or "intro" in blob.lower()
    # No cross-person evidence from other Omars.
    for point in state.final_brief.points:
        for eid in point.evidence_ids:
            assert eid in state.evidence
            text = state.evidence[eid].text.lower()
            assert "lease" not in text or "rachel" in text.lower()
            assert "case competition" not in text
            assert "desautels" not in text


def test_sasha_is_alexandra(synthetic_env, tmp_path: Path) -> None:
    state = asyncio.run(
        run("I'm seeing Sasha soon, catch me up", deps=_deps(tmp_path))
    )
    assert state.status in {"done", "needs_clarification"}
    if state.status == "needs_clarification":
        assert any("Petrova" in c.name or "Alexandra" in c.name for c in state.candidates)
    else:
        assert state.entity_name and "Petrova" in state.entity_name


def test_rachel_northwind_not_ledgerly(synthetic_env, tmp_path: Path) -> None:
    state = asyncio.run(run("I'm meeting Rachel Goldberg", deps=_deps(tmp_path)))
    assert state.status == "done"
    assert state.final_brief is not None
    headline = state.final_brief.headline
    assert "Rachel Goldberg" in headline
    assert "Northwind" in headline or "Northwind" in " ".join(
        p.text for p in state.final_brief.points
    )
    blob = headline + " " + " ".join(p.text for p in state.final_brief.points)
    assert "Ledgerly" not in blob


def test_sarah_whitfield_unknown(synthetic_env, tmp_path: Path) -> None:
    state = asyncio.run(run("I'm meeting Sarah Whitfield", deps=_deps(tmp_path)))
    assert state.status == "needs_clarification"
    assert state.question and "don't know" in state.question.lower()
    assert not state.final_brief or not state.final_brief.points
    assert state.candidates == []


def test_daniel_kim_thursday_calendar(synthetic_env, tmp_path: Path) -> None:
    state = asyncio.run(
        run("I'm meeting Daniel Kim on Thursday", deps=_deps(tmp_path))
    )
    # Calendar should pick Maple Ridge principal first / resolve him.
    if state.status == "needs_clarification":
        assert state.candidates
        assert "Maple" in state.candidates[0].reason or "Ridge" in (
            state.candidates[0].reason + state.candidates[0].name
        ) or "calendar:" in state.candidates[0].reason
        assert "Daniel Kim" in state.candidates[0].name
    else:
        assert state.entity_name == "Daniel Kim"
        assert state.final_brief is not None
        blob = state.final_brief.headline + " ".join(p.text for p in state.final_brief.points)
        assert "Maple" in blob or "Ridge" in blob or "data-room" in blob.lower() or "dataroom" in blob.lower() or "pitch" in blob.lower()


def test_cited_evidence_in_pack(synthetic_env, tmp_path: Path) -> None:
    state = asyncio.run(
        run("I'm meeting Omar Haddad on Wednesday", deps=_deps(tmp_path))
    )
    assert state.final_brief is not None
    for point in state.final_brief.points:
        for eid in point.evidence_ids:
            assert eid in state.evidence
