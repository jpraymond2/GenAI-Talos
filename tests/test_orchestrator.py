"""Orchestrator flow tests with a mocked generator."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from recall.config import get_settings
from recall.llm.generator import reset_client
from recall.orchestrator import Deps, MAX_CRITIC_ROUNDS, run
from recall.schemas import Brief, CriticVerdict, Intent, PointVerdict, TalkingPoint
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


def _brief(*texts: str) -> Brief:
    points = [
        TalkingPoint(text=text, evidence_ids=["e2"], kind="open_loop", priority=i + 1)
        for i, text in enumerate(texts)
    ]
    return Brief(
        person="Karim Haddad",
        headline="Karim Haddad, Ledgerly founder; you last spoke 3 weeks ago on WhatsApp.",
        points=points,
        caveats=[],
    )


def _pass_verdict(n: int) -> CriticVerdict:
    return CriticVerdict(
        overall="pass",
        verdicts=[
            PointVerdict(index=i, decision="keep", reason="ok", fix_hint=None) for i in range(n)
        ],
        missing=[],
        refetch=[],
    )


def _revise_verdict(n: int, *, drop_last: bool = False) -> CriticVerdict:
    verdicts: list[PointVerdict] = []
    for i in range(n):
        if drop_last and i == n - 1:
            verdicts.append(
                PointVerdict(index=i, decision="drop", reason="noise", fix_hint=None)
            )
        elif i == 0:
            verdicts.append(
                PointVerdict(index=i, decision="fix", reason="stale", fix_hint="use relative time")
            )
        else:
            verdicts.append(
                PointVerdict(index=i, decision="keep", reason="ok", fix_hint=None)
            )
    return CriticVerdict(overall="revise", verdicts=verdicts, missing=["open loop"], refetch=[])


def _deps(tracer: Tracer, handler: Any) -> Deps:
    async def generate_json(prompt: str, system: str | None, schema: type[BaseModel], **kwargs: Any):
        return await handler(prompt, system, schema, **kwargs)

    async def generate(*args: Any, **kwargs: Any) -> str:
        return ""

    return Deps(tracer=tracer, generate=generate, generate_json=generate_json)


def test_ambiguous_karim_needs_clarification(tmp_path: Path) -> None:
    calls: list[str] = []

    async def handler(prompt: str, system: str | None, schema: type[BaseModel], **kwargs: Any):
        calls.append(schema.__name__)
        assert schema is Intent
        return Intent(
            kind="brief",
            person_name="Karim",
            when="in 10 minutes",
            action_request=None,
            sources_plan=["whatsapp", "gmail"],
            confidence=0.9,
        )

    tracer = Tracer(logs_dir=tmp_path / "logs")
    state = asyncio.run(run("I'm meeting Karim", deps=_deps(tracer, handler)))

    assert state.status == "needs_clarification"
    assert state.question == "Karim Haddad or Karim Saleh?"
    assert {c.name for c in state.candidates} == {"Karim Haddad", "Karim Saleh"}
    assert calls == ["Intent"]
    assert (tracer.path.parent / "run.json").is_file()


def test_critic_revise_once_triggers_exactly_one_rewrite(tmp_path: Path) -> None:
    writer_calls = {"n": 0}
    critic_calls = {"n": 0}

    async def handler(prompt: str, system: str | None, schema: type[BaseModel], **kwargs: Any):
        if schema is Intent:
            return Intent(
                kind="brief",
                person_name="Karim Haddad",
                when=None,
                action_request=None,
                sources_plan=["whatsapp"],
                confidence=0.95,
            )
        if schema is Brief:
            writer_calls["n"] += 1
            if writer_calls["n"] == 1:
                return _brief("Owe Lina intro.", "Moving to Toronto.", "Habs game.")
            return _brief("Still owe Lina an intro.", "Moving to Toronto in January.")
        if schema is CriticVerdict:
            critic_calls["n"] += 1
            if critic_calls["n"] == 1:
                return _revise_verdict(3, drop_last=True)
            return _pass_verdict(2)
        raise AssertionError(schema)

    tracer = Tracer(logs_dir=tmp_path / "logs")
    state = asyncio.run(run("I'm meeting Karim Haddad", deps=_deps(tracer, handler)))

    assert state.status == "done"
    assert writer_calls["n"] == 2
    assert critic_calls["n"] == 2
    assert state.round == 2
    assert state.final_brief is not None
    assert len(state.final_brief.points) == 2

    events = [
        __import__("json").loads(line)
        for line in tracer.path.read_text(encoding="utf-8").splitlines()
    ]
    round_summaries = [
        e["payload"]["summary"]
        for e in events
        if e["agent"] == "orchestrator" and e["kind"] == "round"
    ]
    assert any("round 2: writer revised 1 points, dropped 1" in s for s in round_summaries)


def test_critic_loop_never_exceeds_two_rounds(tmp_path: Path) -> None:
    critic_calls = {"n": 0}

    async def handler(prompt: str, system: str | None, schema: type[BaseModel], **kwargs: Any):
        if schema is Intent:
            return Intent(
                kind="brief",
                person_name="Karim Haddad",
                when=None,
                action_request=None,
                sources_plan=["whatsapp"],
                confidence=0.9,
            )
        if schema is Brief:
            return _brief("Point A", "Point B")
        if schema is CriticVerdict:
            critic_calls["n"] += 1
            return _revise_verdict(2)
        raise AssertionError(schema)

    tracer = Tracer(logs_dir=tmp_path / "logs")
    state = asyncio.run(run("I'm meeting Karim Haddad", deps=_deps(tracer, handler)))

    assert state.status == "done"
    assert critic_calls["n"] == MAX_CRITIC_ROUNDS
    assert state.round == MAX_CRITIC_ROUNDS
    assert state.round <= 2
    # router + 2 writer + 2 critic
    assert state.context["llm_calls"] == 5


def test_entity_id_skips_karim_ambiguity(tmp_path: Path) -> None:
    async def handler(prompt: str, system: str | None, schema: type[BaseModel], **kwargs: Any):
        if schema is Intent:
            return Intent(
                kind="brief",
                person_name="Karim",
                when=None,
                action_request=None,
                sources_plan=["whatsapp"],
                confidence=0.9,
            )
        if schema is Brief:
            return _brief("Owe Lina intro.")
        if schema is CriticVerdict:
            return _pass_verdict(1)
        raise AssertionError(schema)

    tracer = Tracer(logs_dir=tmp_path / "logs")
    state = asyncio.run(
        run("I'm meeting Karim", entity_id="karim-haddad", deps=_deps(tracer, handler))
    )
    assert state.status == "done"
    assert state.entity_name == "Karim Haddad"
    assert state.final_brief is not None
