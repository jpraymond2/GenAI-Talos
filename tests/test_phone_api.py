"""Phone access API: auth, ask continuation, status, voice mp4."""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from recall.schemas import Brief, Candidate, RunState, TalkingPoint, Utterance
from recall.server import session as session_mod
from recall.server.app import app


@pytest.fixture(autouse=True)
def _clear_sessions() -> None:
    session_mod.clear_runs()
    yield
    session_mod.clear_runs()


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def _clarification_state() -> RunState:
    return RunState(
        run_id="run-clarify-1",
        utterance=Utterance(
            text="I'm meeting Karim",
            ts=datetime.now(timezone.utc),
            mode="text",
        ),
        status="needs_clarification",
        question="Karim Haddad or Karim Saleh?",
        candidates=[
            Candidate(
                entity_id="karim-haddad",
                name="Karim Haddad",
                score=0.9,
                reason="name",
            ),
            Candidate(
                entity_id="karim-saleh",
                name="Karim Saleh",
                score=0.85,
                reason="name",
            ),
        ],
    )


def _done_state(**overrides: Any) -> RunState:
    state = RunState(
        run_id="run-clarify-1",
        utterance=Utterance(
            text="I'm meeting Karim",
            ts=datetime.now(timezone.utc),
            mode="text",
        ),
        entity_id="karim-haddad",
        entity_name="Karim Haddad",
        status="done",
        final_brief=Brief(
            person="Karim Haddad",
            headline="Catch up on the seed round",
            points=[
                TalkingPoint(
                    text="Ledgerly is raising this quarter",
                    evidence_ids=[],
                    kind="catch_up",
                    priority=1,
                ),
                TalkingPoint(
                    text="Open loop on the Lina intro",
                    evidence_ids=[],
                    kind="open_loop",
                    priority=2,
                ),
            ],
        ),
    )
    return state.model_copy(update=overrides)


def test_api_requires_token(client: TestClient) -> None:
    res = client.post("/api/ask", json={"text": "hi"})
    assert res.status_code == 401


def test_ask_clarification_then_continue(
    client: TestClient,
    api_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _clarification_state()
    second = _done_state()
    mock_run = AsyncMock(side_effect=[first, second])
    monkeypatch.setattr("recall.orchestrator.run", mock_run)

    r1 = client.post(
        "/api/ask",
        headers=api_headers,
        json={"text": "I'm meeting Karim", "run_id": None},
    )
    assert r1.status_code == 200
    body1 = r1.json()
    assert body1["status"] == "needs_clarification"
    assert body1["run_id"] == "run-clarify-1"
    assert "speech" in body1

    r2 = client.post(
        "/api/ask",
        headers=api_headers,
        json={"text": "Haddad", "run_id": "run-clarify-1"},
    )
    assert r2.status_code == 200
    body2 = r2.json()
    assert body2["status"] == "done"
    assert body2["run_id"] == "run-clarify-1"
    speech = body2["speech"]
    assert isinstance(speech, str) and speech
    assert len(speech.split()) <= 90
    assert "Catch up" in speech or "seed" in speech.lower() or "Ledgerly" in speech


def test_status_endpoint(client: TestClient, api_headers: dict[str, str]) -> None:
    res = client.get("/api/status", headers=api_headers)
    assert res.status_code == 200
    body = res.json()
    assert "last_ingest_at" in body
    assert "totals" in body
    assert isinstance(body["totals"], dict)


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not on PATH")
def test_voice_tiny_mp4(
    client: TestClient,
    api_headers: dict[str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import subprocess

    wav = tmp_path / "silence.wav"
    mp4 = tmp_path / "silence.mp4"
    # 0.2s of silence as wav then remux/encode to mp4 aac
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=16000:cl=mono",
            "-t",
            "0.2",
            str(wav),
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(wav), "-c:a", "aac", str(mp4)],
        check=True,
        capture_output=True,
    )

    monkeypatch.setattr(
        "recall.voice.stt.transcribe_detailed",
        lambda *_a, **_k: ("I'm meeting Karim Haddad", "sample"),
    )
    monkeypatch.setattr(
        "recall.orchestrator.run",
        AsyncMock(return_value=_done_state()),
    )

    res = client.post(
        "/api/voice",
        headers=api_headers,
        files={"audio": ("utterance.mp4", mp4.read_bytes(), "audio/mp4")},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["transcript"]
    assert body["status"] == "done"
    assert "speech" in body
    assert len(str(body["speech"]).split()) <= 90
