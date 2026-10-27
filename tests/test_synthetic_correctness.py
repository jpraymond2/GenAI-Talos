"""Offline-path correctness on the synthetic Jordan Lefebvre world."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest
from pydantic import BaseModel

from recall.config import get_settings
from recall.data.duckdb_backend import DuckDBBackend
from recall.data.query_layer import clear_default_query_layer
from recall.engines.world import world as world_facade
from recall.llm.generator import reset_client
from recall.orchestrator import build_deps, run
from recall.schemas import Brief, CriticVerdict, Intent, PointVerdict, TalkingPoint

_ISO_Z = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
_FACT_KEYWORDS = json.loads(
    Path("data/synthetic/ground_truth/fact_keywords.json").read_text(encoding="utf-8")
)


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


def _brief_text(brief: Brief) -> str:
    return " ".join(
        [brief.headline, *[p.text for p in brief.points], *brief.caveats]
    ).lower()


def _covers(fact_id: str, text: str) -> bool:
    groups = _FACT_KEYWORDS.get(fact_id) or []
    for group in groups:
        if not any(str(kw).lower() in text for kw in group):
            return False
    return bool(groups)


def _assert_point_quality(brief: Brief) -> None:
    for point in brief.points:
        assert len(point.text.split()) >= 6, point.text
        assert not _ISO_Z.search(point.text), point.text


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
    assert "Omar Haddad" in state.final_brief.person
    text = _brief_text(state.final_brief)
    assert _covers("omar_h.intro_ask", text)
    assert _covers("omar_h.coffee", text)
    headline_l = state.final_brief.headline.lower()
    assert "earlier today on calendar" not in headline_l
    assert "last contact" in headline_l
    assert "calendar" not in headline_l.split("last contact", 1)[1].split(";")[0]
    assert "next meeting" in headline_l
    _assert_point_quality(state.final_brief)
    for point in state.final_brief.points:
        for eid in point.evidence_ids:
            assert eid in state.evidence


def test_headline_tokens_are_grounded(synthetic_env, tmp_path: Path) -> None:
    """Headline role/org words must come from contacts / WM attrs / conflicts."""
    state = asyncio.run(run("I'm meeting Tom Becker", deps=_deps(tmp_path)))
    assert state.status == "done"
    assert state.final_brief is not None
    headline = state.final_brief.headline
    banned = [
        "parfait",
        "thanks omar",
        "moving you to bcc",
        "amazing",
        "will send it by tuesday",
    ]
    lowered = headline.lower()
    for bad in banned:
        assert bad not in lowered, headline
    _assert_point_quality(state.final_brief)


def test_priya_employer_fact(synthetic_env, tmp_path: Path) -> None:
    state = asyncio.run(run("Where does Priya work now?", deps=_deps(tmp_path)))
    assert state.status == "done"
    assert state.final_brief is not None
    text = _brief_text(state.final_brief)
    assert _covers("priya.job", text)
    assert "stellarfin" in state.final_brief.headline.lower()
    assert "brightloop" in state.final_brief.headline.lower()
    _assert_point_quality(state.final_brief)


def test_lucas_location_facts(synthetic_env, tmp_path: Path) -> None:
    state = asyncio.run(run("Where does Lucas live?", deps=_deps(tmp_path)))
    assert state.status == "done"
    assert state.final_brief is not None
    text = _brief_text(state.final_brief)
    assert _covers("lucas.paris", text)
    assert "paris" in state.final_brief.headline.lower()
    _assert_point_quality(state.final_brief)


def test_mei_model_promise(synthetic_env, tmp_path: Path) -> None:
    state = asyncio.run(
        run("Sync with Mei tomorrow, what's open between us?", deps=_deps(tmp_path))
    )
    assert state.status == "done"
    assert state.final_brief is not None
    text = _brief_text(state.final_brief)
    assert _covers("mei.model_ask", text) or "model" in text
    _assert_point_quality(state.final_brief)


def test_sasha_is_alexandra(synthetic_env, tmp_path: Path) -> None:
    state = asyncio.run(
        run("I'm seeing Sasha soon, catch me up", deps=_deps(tmp_path))
    )
    assert state.status in {"done", "needs_clarification"}
    if state.status == "needs_clarification":
        assert any("Petrova" in c.name or "Alexandra" in c.name for c in state.candidates)
    else:
        assert state.entity_name and "Petrova" in state.entity_name
        assert state.final_brief is not None
        _assert_point_quality(state.final_brief)


def test_rachel_northwind_not_ledgerly(synthetic_env, tmp_path: Path) -> None:
    state = asyncio.run(run("I'm meeting Rachel Goldberg", deps=_deps(tmp_path)))
    assert state.status == "done"
    assert state.final_brief is not None
    assert "Rachel Goldberg" in state.final_brief.headline
    assert "Northwind" in state.final_brief.headline or "Northwind" in _brief_text(
        state.final_brief
    )
    assert "Ledgerly" not in _brief_text(state.final_brief)
    assert "on calendar" not in state.final_brief.headline.lower() or "next meeting" in state.final_brief.headline.lower()
    _assert_point_quality(state.final_brief)


def test_sarah_whitfield_unknown(synthetic_env, tmp_path: Path) -> None:
    state = asyncio.run(run("I'm meeting Sarah Whitfield", deps=_deps(tmp_path)))
    assert state.status == "needs_clarification"
    assert state.question and "don't know" in state.question.lower()
    assert state.candidates == []


def test_daniel_kim_thursday_calendar(synthetic_env, tmp_path: Path) -> None:
    state = asyncio.run(
        run("I'm meeting Daniel Kim on Thursday", deps=_deps(tmp_path))
    )
    assert state.status == "done"
    assert state.entity_name == "Daniel Kim"
    assert state.final_brief is not None
    blob = _brief_text(state.final_brief)
    assert "maple" in blob or "ridge" in blob or "pitch" in blob or "data" in blob
    assert "parfait" not in state.final_brief.headline.lower()
    _assert_point_quality(state.final_brief)


def test_headline_uses_structured_fields_only(synthetic_env, tmp_path: Path) -> None:
    state = asyncio.run(run("I'm meeting Tom Becker", deps=_deps(tmp_path)))
    assert state.status == "done"
    assert state.final_brief is not None
    headline = state.final_brief.headline.lower()
    assert "thanks omar" not in headline
    assert "moving you to bcc" not in headline
    assert "parfait" not in headline
    _assert_point_quality(state.final_brief)


def test_tom_becker_sparse_brief_has_intro_and_past_meeting(
    synthetic_env, tmp_path: Path
) -> None:
    """Sparse contacts still get 1–2 grounded points + last-met headline."""
    state = asyncio.run(run("I'm meeting Tom Becker", deps=_deps(tmp_path)))
    assert state.status == "done"
    assert state.final_brief is not None
    brief = state.final_brief
    assert 1 <= len(brief.points) <= 3
    headline = brief.headline.lower()
    assert "last met" in headline and "feb" in headline and "calendar" in headline
    assert "last message" in headline and "dec" in headline and "gmail" in headline
    blob = _brief_text(brief)
    assert "bonaventure" in blob or "coffee" in blob
    assert "connecting" in blob or "intro" in blob or "lumen" in blob
    assert not any("no reply yet" in p.text.lower() for p in brief.points)
    _assert_point_quality(brief)


def test_omar_connecting_intro_not_open_loop(synthetic_env, tmp_path: Path) -> None:
    """Intro email where Tom already replied is not an owner open loop."""
    state = asyncio.run(
        run("I'm meeting Omar Haddad on Wednesday", deps=_deps(tmp_path))
    )
    assert state.status == "done"
    assert state.final_brief is not None
    for point in state.final_brief.points:
        lowered = point.text.lower()
        if "connecting you" in lowered or "lumen grid" in lowered:
            assert "no reply yet" not in lowered, point.text
    # Rachel intro ask must still surface.
    assert _covers("omar_h.intro_ask", _brief_text(state.final_brief))
    _assert_point_quality(state.final_brief)


def test_revision_adds_missing_evidence(synthetic_env, tmp_path: Path) -> None:
    """When the critic reports missing=[eX], round-2 brief must cite eX."""
    calls = {"writer": 0}

    async def handler(prompt: str, system: str | None, schema: type[BaseModel], **kwargs):
        if schema is Intent:
            return Intent(
                kind="brief",
                person_name="Omar Haddad",
                when=None,
                confidence=0.95,
                sources_plan=["gmail", "calendar"],
            )
        if schema is Brief:
            calls["writer"] += 1
            if calls["writer"] == 1:
                return Brief(
                    person="Omar Haddad",
                    headline="Omar Haddad at Payloop; last contact yesterday on gmail",
                    points=[
                        TalkingPoint(
                            text="Catch up on the latest investor update draft review soon",
                            evidence_ids=["e3"],
                            kind="catch_up",
                            priority=1,
                        )
                    ],
                    caveats=[],
                )
            # Round 2 must cite the missing open-loop id.
            return Brief(
                person="Omar Haddad",
                headline="Omar Haddad at Payloop; last contact yesterday on gmail",
                points=[
                    TalkingPoint(
                        text="Omar asked you for an intro to Rachel Goldberg and you have not replied yet",
                        evidence_ids=["e1"],
                        kind="open_loop",
                        priority=1,
                    ),
                    TalkingPoint(
                        text="Catch up on the latest investor update draft review soon",
                        evidence_ids=["e3"],
                        kind="catch_up",
                        priority=2,
                    ),
                ],
                caveats=[],
            )
        if schema is CriticVerdict:
            if calls["writer"] == 1:
                return CriticVerdict(
                    overall="revise",
                    verdicts=[
                        PointVerdict(index=0, decision="keep", reason="ok", fix_hint=None)
                    ],
                    missing=["e1"],
                    refetch=[],
                )
            return CriticVerdict(
                overall="pass",
                verdicts=[
                    PointVerdict(index=0, decision="keep", reason="ok", fix_hint=None),
                    PointVerdict(index=1, decision="keep", reason="ok", fix_hint=None),
                ],
                missing=[],
                refetch=[],
            )
        raise AssertionError(schema)

    async def generate_json(prompt, system, schema, **kwargs):
        return await handler(prompt, system, schema, **kwargs)

    async def generate(*args, **kwargs):
        return ""

    deps = build_deps(
        sample_mode=True,
        logs_dir=tmp_path / "logs",
        generate=generate,
        generate_json=generate_json,
    )
    state = asyncio.run(run("I'm meeting Omar Haddad", deps=deps))
    assert state.status == "done"
    assert state.final_brief is not None
    cited = {eid for p in state.final_brief.points for eid in p.evidence_ids}
    assert "e1" in cited
    assert calls["writer"] >= 2
