"""LinkedIn evidence source + tags for agents (no LinkedIn connector)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from recall.agents.writer import format_evidence_list
from recall.config import get_settings
from recall.fixtures import load_fixture_evidence
from recall.llm.generator import reset_client
from recall.orchestrator import Deps, run
from recall.schemas import Brief, CriticVerdict, Evidence, Intent, PointVerdict, TalkingPoint
from recall.tracing import Tracer


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


def test_linkedin_evidence_with_tags_validates() -> None:
    item = Evidence.model_validate(
        {
            "id": "e13",
            "source": "linkedin",
            "source_ref": "linkedin_email:fixture-13",
            "ts": "2026-10-09T12:00:00Z",
            "text": "Karim Haddad started a new position: Founder & CEO at Ledgerly.",
            "tags": ["job_change"],
        }
    )
    assert item.source == "linkedin"
    assert item.tags == ["job_change"]


def test_format_evidence_list_includes_job_change_tag() -> None:
    pack = load_fixture_evidence("Karim Haddad")
    lines = format_evidence_list(pack).splitlines()
    e13 = next(line for line in lines if line.startswith("[e13]"))
    assert "linkedin" in e13
    assert "(job_change)" in e13
    assert "Ledgerly" in e13


def test_sanitize_brief_keeps_e13_drops_e14() -> None:
    from recall.agents.job_change import sanitize_brief_job_changes
    from recall.schemas import Brief, TalkingPoint

    pack = load_fixture_evidence("Karim Haddad")
    brief = Brief(
        person="Karim Haddad",
        headline="test",
        points=[
            TalkingPoint(
                text="Congrats on both roles.",
                evidence_ids=["e13", "e14"],
                kind="news",
                priority=1,
            ),
            TalkingPoint(
                text="Intro still open.",
                evidence_ids=["e2"],
                kind="open_loop",
                priority=2,
            ),
        ],
        caveats=["Also Northbridge Bank role unclear."],
    )
    cleaned = sanitize_brief_job_changes(brief, pack)
    cited = {eid for point in cleaned.points for eid in point.evidence_ids}
    assert "e13" in cited
    assert "e14" not in cited
    assert not any("Northbridge" in c for c in cleaned.caveats)


def test_orchestrator_runs_with_linkedin_fixture(tmp_path: Path) -> None:
    async def handler(prompt: str, system: str | None, schema: type[BaseModel], **kwargs: Any):
        if schema is Intent:
            return Intent(
                kind="brief",
                person_name="Karim Haddad",
                when=None,
                action_request=None,
                sources_plan=[
                    "gmail",
                    "imessage",
                    "whatsapp",
                    "linkedin",
                    "calendar",
                    "web",
                    "memory",
                ],
                confidence=0.95,
            )
        if schema is Brief:
            return Brief(
                person="Karim Haddad",
                headline="Karim Haddad, Ledgerly founder; you last spoke 3 weeks ago.",
                points=[
                    TalkingPoint(
                        text="Congrats on the Founder and CEO role at Ledgerly.",
                        evidence_ids=["e13"],
                        kind="news",
                        priority=1,
                    ),
                    TalkingPoint(
                        text="You still owe him an intro to Lina Chen.",
                        evidence_ids=["e2"],
                        kind="open_loop",
                        priority=2,
                    ),
                ],
                caveats=[],
            )
        if schema is CriticVerdict:
            return CriticVerdict(
                overall="pass",
                verdicts=[
                    PointVerdict(
                        index=0,
                        decision="keep",
                        reason="job_change at Ledgerly within 60 days.",
                        fix_hint=None,
                    ),
                    PointVerdict(
                        index=1,
                        decision="keep",
                        reason="Supported by e2.",
                        fix_hint=None,
                    ),
                ],
                missing=[],
                refetch=[],
            )
        raise AssertionError(schema)

    async def generate(*args: Any, **kwargs: Any) -> str:
        return ""

    async def generate_json(
        prompt: str, system: str | None, schema: type[BaseModel], **kwargs: Any
    ) -> BaseModel:
        return await handler(prompt, system, schema, **kwargs)

    tracer = Tracer(logs_dir=tmp_path / "logs")
    deps = Deps(tracer=tracer, generate=generate, generate_json=generate_json)
    state = asyncio.run(run("I'm meeting Karim Haddad", deps=deps))

    assert state.status == "done"
    assert state.final_brief is not None
    assert "e13" in state.evidence
    assert "e14" in state.evidence
    assert state.evidence["e13"].source == "linkedin"
    cited = {eid for point in state.final_brief.points for eid in point.evidence_ids}
    assert "e13" in cited
    assert "e14" not in cited
