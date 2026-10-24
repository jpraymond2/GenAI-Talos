"""Orchestrator + World Model + H-MEM integration (mocked LLM)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel

from recall.config import get_settings
from recall.llm.generator import reset_client
from recall.orchestrator import build_deps, run
from recall.schemas import Brief, CriticVerdict, Intent, PointVerdict, TalkingPoint


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("SAMPLE_MODE", "true")
    monkeypatch.setenv("NVIDIA_API_KEY", "")
    monkeypatch.setenv("LOGS_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("CACHE_DIR", str(Path("data/cache").resolve()))
    get_settings.cache_clear()
    reset_client()
    yield
    get_settings.cache_clear()
    reset_client()


def _pass_brief(person: str = "Karim Haddad") -> Brief:
    return Brief(
        person=person,
        headline=f"{person}; last spoke recently.",
        points=[
            TalkingPoint(
                text="You owe them a reply about coffee.",
                evidence_ids=["e1"],
                kind="open_loop",
                priority=1,
            )
        ],
        caveats=[],
    )


def _pass_verdict(n: int = 1) -> CriticVerdict:
    return CriticVerdict(
        overall="pass",
        verdicts=[
            PointVerdict(index=i, decision="keep", reason="ok", fix_hint=None) for i in range(n)
        ],
        missing=[],
        refetch=[],
    )


def _handler_factory(
    *,
    person_name: str,
    kind: str = "brief",
    brief: Brief | None = None,
):
    async def handler(
        prompt: str, system: str | None, schema: type[BaseModel], **kwargs: Any
    ):
        if schema is Intent:
            return Intent(
                kind=kind,  # type: ignore[arg-type]
                person_name=person_name,
                when=None,
                action_request=None,
                sources_plan=["whatsapp", "gmail", "memory"],
                confidence=0.95,
            )
        if schema is Brief:
            return brief or _pass_brief(person_name)
        if schema is CriticVerdict:
            n = len((brief or _pass_brief(person_name)).points)
            return _pass_verdict(n)
        raise AssertionError(schema)

    return handler


def _deps(tmp_path: Path, handler: Any):
    async def generate_json(
        prompt: str, system: str | None, schema: type[BaseModel], **kwargs: Any
    ):
        return await handler(prompt, system, schema, **kwargs)

    async def generate(*args: Any, **kwargs: Any) -> str:
        return ""

    return build_deps(
        sample_mode=True,
        logs_dir=tmp_path / "logs",
        generate=generate,
        generate_json=generate_json,
    )


def test_karim_needs_clarification_then_haddad_continues(tmp_path: Path) -> None:
    handler = _handler_factory(person_name="Karim")
    deps = _deps(tmp_path, handler)

    state = asyncio.run(run("I'm meeting Karim", deps=deps))
    assert state.status == "needs_clarification"
    assert len(state.candidates) >= 2
    run_id = state.run_id

    # Fresh deps sharing logs_dir so state.json is found; same run_id.
    deps2 = _deps(tmp_path, _handler_factory(person_name="Karim Haddad"))
    continued = asyncio.run(
        run(
            "Haddad",
            run_id=run_id,
            clarification_reply="Haddad",
            deps=deps2,
        )
    )
    assert continued.run_id == run_id
    assert continued.status == "done"
    assert continued.entity_name and "Haddad" in continued.entity_name
    assert continued.final_brief is not None


def test_karim_haddad_evidence_from_world_model(tmp_path: Path) -> None:
    deps = _deps(tmp_path, _handler_factory(person_name="Karim Haddad"))
    state = asyncio.run(run("I'm meeting Karim Haddad", deps=deps))

    assert state.status == "done"
    assert state.evidence
    # Open loop + conflict from World Model (not fixture files).
    open_loops = [
        e for e in state.evidence.values() if "open_loop" in (e.tags or [])
    ]
    conflicts = [
        e for e in state.evidence.values() if "conflict" in (e.tags or [])
    ]
    assert open_loops, "expected open_loop evidence from world person_context"
    assert conflicts, "expected conflict evidence from world person_context"
    for eid, ev in state.evidence.items():
        assert "tests/fixtures" not in (ev.source_ref or "")
        assert "evidence_karim" not in (ev.source_ref or "")


def test_note_then_brief_includes_memory_evidence(tmp_path: Path) -> None:
    note_text = (
        "Karim Haddad told me he's moving to Toronto in January"
    )
    deps_note = _deps(
        tmp_path,
        _handler_factory(person_name="Karim Haddad", kind="note"),
    )
    noted = asyncio.run(run(note_text, deps=deps_note, force_kind="note"))
    assert noted.status == "done"
    assert noted.final_brief is not None
    assert noted.final_brief.headline.lower().startswith("noted")

    # Brief for the same person should surface the mined memory as m*.
    brief_handler = _handler_factory(person_name="Karim Haddad")
    deps_brief = _deps(tmp_path, brief_handler)
    # Reuse same hmem DB (sample path) — mining already wrote there.
    state = asyncio.run(run("I'm meeting Karim Haddad", deps=deps_brief))
    assert state.status == "done"
    memory_items = [
        e for eid, e in state.evidence.items() if eid.startswith("m") or e.source == "memory"
    ]
    assert memory_items, "expected memory evidence after noting"


def test_hmem_retrieve_failure_still_returns_brief(tmp_path: Path) -> None:
    deps = _deps(tmp_path, _handler_factory(person_name="Karim Haddad"))
    deps.hmem_retrieval.retrieve = AsyncMock(  # type: ignore[method-assign]
        side_effect=RuntimeError("hmem down")
    )
    state = asyncio.run(run("I'm meeting Karim Haddad", deps=deps))
    assert state.status == "done"
    assert state.final_brief is not None
    caveats = " ".join(state.final_brief.caveats).lower()
    assert "memor" in caveats
