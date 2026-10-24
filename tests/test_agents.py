"""Smoke tests for router, writer, and critic agents (sample provider)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from recall.agents import critic, router, writer
from recall.config import get_settings
from tests.helpers_fixtures import load_fixture_evidence
from recall.llm.generator import reset_client
from recall.schemas import RunState, Utterance
from recall.tracing import Tracer


@pytest.fixture(autouse=True)
def _sample_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SAMPLE_MODE", "true")
    monkeypatch.setenv("NVIDIA_API_KEY", "")
    monkeypatch.setenv("LOGS_DIR", str(tmp_path / "logs"))
    # Use repo cache packs for router/writer/critic defaults.
    monkeypatch.setenv("CACHE_DIR", str(Path("data/cache").resolve()))
    get_settings.cache_clear()
    reset_client()
    yield
    get_settings.cache_clear()
    reset_client()


def _state() -> RunState:
    return RunState(
        run_id="test-run",
        utterance=Utterance(
            text="I'm meeting Karim",
            ts=datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc),
            mode="text",
        ),
    )


def test_router_writer_critic_sample_pipeline(tmp_path: Path) -> None:
    tracer = Tracer(logs_dir=tmp_path / "logs")
    deps = SimpleNamespace(tracer=tracer)
    state = _state()

    with tracer.activate():
        state = asyncio.run(router.run(state, deps))
        assert state.intent is not None
        assert state.intent.kind == "brief"
        assert state.intent.person_name == "Karim"

        state.entity_name = "Karim Haddad"
        state.evidence = load_fixture_evidence("Karim Haddad")
        state = asyncio.run(writer.run(state, deps))
        assert len(state.drafts) == 1
        assert state.drafts[0].points

        state = asyncio.run(critic.run(state, deps))
        assert state.verdicts[-1].overall == "pass"
        assert state.final_brief is state.drafts[-1]

    lines = tracer.path.read_text(encoding="utf-8").strip().splitlines()
    agents = {__import__("json").loads(line)["agent"] for line in lines}
    assert {"router", "writer", "critic"}.issubset(agents)
    summaries = [
        __import__("json").loads(line)["payload"]["summary"]
        for line in lines
        if __import__("json").loads(line)["agent"] in {"router", "writer", "critic"}
    ]
    assert any(s.startswith("kind=") for s in summaries)
    assert any(s.startswith("person=") for s in summaries)
    assert any(s.startswith("overall=") for s in summaries)


def test_format_evidence_list_compact() -> None:
    pack = load_fixture_evidence("Karim Haddad")
    lines = writer.format_evidence_list(pack).splitlines()
    assert any(line.startswith("[e1] whatsapp ") and "Ledgerly" in line for line in lines)
    assert "2026-" in lines[0] or "2025-" in lines[0]


def test_today_line_format() -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from recall.agents.time_context import today_line, with_today

    fixed = datetime(2026, 10, 22, 15, 30, tzinfo=ZoneInfo("America/Toronto"))
    line = today_line(now=fixed)
    assert line == "TODAY: 2026-10-22, Thursday, time zone America/Toronto"
    assert with_today("UTTERANCE: hi", now=fixed).startswith(line + "\n")


def test_agents_prepend_today_to_user_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[str] = []

    async def fake_generate_json(prompt: str, system: str | None, schema: type, **kwargs: object):
        captured.append(prompt)
        from recall.schemas import Brief, CriticVerdict, Intent

        if schema is Intent:
            return Intent(
                kind="brief",
                person_name="Karim",
                when=None,
                action_request=None,
                sources_plan=["memory"],
                confidence=0.9,
            )
        if schema is Brief:
            return Brief(
                person="Karim Haddad",
                headline="Karim Haddad, Ledgerly founder; you last spoke 3 weeks ago on WhatsApp.",
                points=[],
                caveats=[],
            )
        if schema is CriticVerdict:
            return CriticVerdict(overall="pass", verdicts=[], missing=[], refetch=[])
        raise AssertionError(f"unexpected schema {schema}")

    monkeypatch.setattr("recall.agents.router.generate_json", fake_generate_json)
    monkeypatch.setattr("recall.agents.writer.generate_json", fake_generate_json)
    monkeypatch.setattr("recall.agents.critic.generate_json", fake_generate_json)

    deps = SimpleNamespace(tracer=None)
    state = _state()
    state = asyncio.run(router.run(state, deps))
    state.entity_name = "Karim Haddad"
    state.evidence = load_fixture_evidence("Karim Haddad")
    state = asyncio.run(writer.run(state, deps))
    state = asyncio.run(critic.run(state, deps))

    assert len(captured) == 3
    for prompt in captured:
        assert prompt.startswith("TODAY: ")
        assert "time zone America/Toronto" in prompt.splitlines()[0]
    assert "relative times" in captured[1]
    assert "staleness relative to TODAY" in captured[2]
