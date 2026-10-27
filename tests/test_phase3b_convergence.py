"""Phase 3b: critic convergence, round diffs, non-empty trace summaries."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from recall.config import get_settings
from recall.data.duckdb_backend import DuckDBBackend
from recall.data.query_layer import clear_default_query_layer
from recall.engines import hmem as hmem_mod
from recall.engines.world import world as world_facade
from recall.llm.generator import reset_client
from recall.orchestrator import build_deps, run
from recall.schemas import Brief, CriticVerdict, Evidence, PointVerdict, TalkingPoint
from recall.agents.offline import critique_brief
from datetime import datetime, timezone


@pytest.fixture()
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
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
    yield
    world_facade.reset()
    hmem_mod.hmem.reset()
    clear_default_query_layer()
    DuckDBBackend.clear_connection_cache()
    get_settings.cache_clear()
    reset_client()


def _deps(tmp_path: Path, run_id: str = "p3b"):
    return build_deps(sample_mode=True, logs_dir=tmp_path / "logs", run_id=run_id)


def test_two_rounds_never_reflag_present_or_just_dropped(env, tmp_path: Path) -> None:
    """After round 1 cites e1, round 2 must not flag e1; dropped e2 stays dropped."""
    now = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)
    pack = {
        "e1": Evidence(
            id="e1",
            source="imessage",
            source_ref="m1",
            ts=now,
            text="can you send me the revised financial model by Tuesday please?",
            tags=["open_loop", "promise"],
        ),
        "e2": Evidence(
            id="e2",
            source="imessage",
            source_ref="m2",
            ts=now,
            text="early stage climate fund details I'll send later this week for sure",
            tags=["open_loop", "promise"],
        ),
        "e3": Evidence(
            id="e3",
            source="imessage",
            source_ref="m3",
            ts=now,
            text="pipeline demo works end to end now with hydrolab sample data loading fast",
            tags=[],
        ),
        "e4": Evidence(
            id="e4",
            source="calendar",
            source_ref="c1",
            ts=now,
            text="Tidepool weekly sync",
            tags=["upcoming"],
        ),
    }
    from recall.schemas import RunState, Utterance

    draft1 = Brief(
        person="Mei Tanaka",
        headline="Mei",
        points=[
            TalkingPoint(
                text="You told Mei on Tue you'd send the revised financial model by Tuesday; still open.",
                evidence_ids=["e1"],
                kind="open_loop",
                priority=1,
            ),
            TalkingPoint(
                text="Latest thread on Sun: pipeline demo works end to end now with hydrolab sample.",
                evidence_ids=["e3"],
                kind="catch_up",
                priority=2,
            ),
        ],
    )
    state = RunState(
        run_id="t1",
        utterance=Utterance(text="Sync with Mei tomorrow", ts=now, mode="text"),
        entity_name="Mei Tanaka",
        evidence=pack,
        drafts=[draft1],
        round=1,
    )
    v1 = critique_brief(state)
    # e1 already cited (owner-owed). Lowest kept is catch-up (rank 5).
    # e2 is also rank 1 — same/higher than lowest kept? rank 1 < 5 so it CAN flag e2.
    # After citing e1, e2 same priority as e1's rank... lowest_kept is max of kept ranks = 5.
    # e2 rank 1 < 5 → would flag. For the test we want: never reflag e1.
    assert "e1" not in v1.missing

    # Simulate round 2 draft that added e2 and dropped e3.
    draft2 = Brief(
        person="Mei Tanaka",
        headline="Mei",
        points=[
            TalkingPoint(
                text="You told Mei on Tue you'd send the revised financial model by Tuesday; still open.",
                evidence_ids=["e1"],
                kind="open_loop",
                priority=1,
            ),
            TalkingPoint(
                text="You told Mei you'd send early stage climate fund details; still open.",
                evidence_ids=["e2"],
                kind="open_loop",
                priority=2,
            ),
            TalkingPoint(
                text="Upcoming: Tidepool weekly sync on Mon (calendar) with the team.",
                evidence_ids=["e4"],
                kind="logistics",
                priority=3,
            ),
        ],
    )
    state2 = state.model_copy(
        update={"drafts": [draft1, draft2], "verdicts": [v1], "round": 2}
    )
    v2 = critique_brief(state2)
    assert "e1" not in v2.missing
    assert "e3" not in v2.missing  # just dropped — do not re-flag as missing
    assert v2.overall in {"pass", "unresolved"}
    assert v2.overall != "revise"


def test_sync_mei_revises_once_then_passes(env, tmp_path: Path) -> None:
    deps = _deps(tmp_path, run_id="mei-rev")
    state = asyncio.run(run("Sync with Mei tomorrow", deps=deps, run_id="mei-rev"))
    assert state.round == 2
    assert state.verdicts
    assert state.verdicts[-1].overall in {"pass", "unresolved"}
    assert state.verdicts[-1].overall != "revise"
    # Round summary should mention real point diffs.
    trace = Path(tmp_path / "logs" / state.run_id / "trace.jsonl").read_text()
    assert "added" in trace.lower()
    assert "dropped" in trace.lower() or "changed" in trace.lower()


def test_omar_brief_passes_first_round(env, tmp_path: Path) -> None:
    deps = _deps(tmp_path, run_id="omar-pass")
    state = asyncio.run(
        run("I'm meeting Omar Haddad on Wednesday", deps=deps, run_id="omar-pass")
    )
    assert state.round == 1
    assert state.verdicts[-1].overall == "pass"


def test_trace_summaries_nonempty(env, tmp_path: Path) -> None:
    deps = _deps(tmp_path, run_id="sum-check")
    state = asyncio.run(
        run("I'm meeting Omar Haddad on Wednesday", deps=deps, run_id="sum-check")
    )
    path = tmp_path / "logs" / state.run_id / "trace.jsonl"
    rows = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    interesting = {
        ("query_layer", "query"),
        ("query_layer", "fallback"),
        ("world", "resolve"),
        ("world", "person_context"),
        ("hmem", "retrieve"),
        ("llm", "embed"),
        ("orchestrator", "retrieve"),
    }
    seen = set()
    for row in rows:
        key = (row.get("agent"), row.get("kind"))
        if key not in interesting:
            continue
        summary = (row.get("payload") or {}).get("summary")
        assert isinstance(summary, str) and summary.strip(), row
        seen.add(key)
    assert ("world", "resolve") in seen or ("orchestrator", "resolve") in seen
    assert ("world", "person_context") in seen
    assert ("hmem", "retrieve") in seen


def test_critic_reasons_not_generic(env, tmp_path: Path) -> None:
    deps = _deps(tmp_path, run_id="reasons")
    state = asyncio.run(
        run("I'm meeting Omar Haddad on Wednesday", deps=deps, run_id="reasons")
    )
    for v in state.verdicts[-1].verdicts:
        if v.decision == "keep":
            assert "Cited evidence in pack" not in v.reason
            assert v.reason.lower().startswith("keep:")
