"""Dashboard REST API: ask / confirm / person / memories (no live Zetaris)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from recall.schemas import (
    Brief,
    Candidate,
    RunState,
    TalkingPoint,
    Utterance,
)
from recall.server.app import app


def _done_state(**overrides: Any) -> RunState:
    state = RunState(
        run_id="run-test-1",
        utterance=Utterance(
            text="I'm meeting Karim Haddad",
            ts=datetime.now(timezone.utc),
            mode="text",
        ),
        entity_id="karim-haddad",
        entity_name="Karim Haddad",
        status="done",
        final_brief=Brief(
            person="Karim Haddad",
            headline="Catch up with Karim",
            points=[
                TalkingPoint(
                    text="Seed round update",
                    evidence_ids=[],
                    kind="catch_up",
                    priority=1,
                )
            ],
        ),
        candidates=[],
        evidence={},
        verdicts=[],
        action_plan=None,
        question=None,
    )
    return state.model_copy(update=overrides)


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def test_api_ask_returns_run_state(
    client: TestClient,
    api_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = _done_state()
    monkeypatch.setattr(
        "recall.orchestrator.run",
        AsyncMock(return_value=expected),
    )
    response = client.post(
        "/api/ask",
        headers=api_headers,
        json={"text": "I'm meeting Karim Haddad"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] == "run-test-1"
    assert body["status"] == "done"
    assert body["entity_id"] == "karim-haddad"
    assert body["final_brief"]["headline"] == "Catch up with Karim"
    assert body["question"] is None
    assert "speech" in body
    assert isinstance(body["candidates"], list)
    assert isinstance(body["evidence"], dict)
    assert isinstance(body["verdicts"], list)
    assert body["action_plan"] is None


def test_api_ask_passes_entity_id(
    client: TestClient,
    api_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_run = AsyncMock(return_value=_done_state(entity_id="karim-saleh"))
    monkeypatch.setattr("recall.orchestrator.run", mock_run)
    response = client.post(
        "/api/ask",
        headers=api_headers,
        json={"text": "I'm meeting Karim", "entity_id": "karim-saleh"},
    )
    assert response.status_code == 200
    mock_run.assert_awaited_once()
    assert mock_run.await_args.kwargs.get("entity_id") == "karim-saleh"


def test_api_confirm_dry_run(
    client: TestClient, api_headers: dict[str, str]
) -> None:
    response = client.post(
        "/api/confirm",
        headers=api_headers,
        json={"run_id": "run-test-1", "confirmed": True},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] in {"dry_run", "cancelled", "not_enabled", "ok"}
    assert body.get("run_id") == "run-test-1"


def test_api_person(
    client: TestClient,
    api_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "recall.engines.world.world.person_context",
        lambda entity_id, budget_tokens=1500: {
            "entity": {"id": entity_id, "canonical_name": "Karim Haddad", "aliases": []},
            "aliases": ["Karim"],
            "conflicts": [],
        },
    )
    monkeypatch.setattr("recall.engines.world.world.reset", lambda: None)
    response = client.get("/api/person/karim-haddad", headers=api_headers)
    assert response.status_code == 200
    assert response.json()["entity"]["canonical_name"] == "Karim Haddad"


def test_api_person_404(
    client: TestClient,
    api_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("recall.engines.world.world.person_context", lambda *_a, **_k: {})
    monkeypatch.setattr("recall.engines.world.world.reset", lambda: None)
    response = client.get("/api/person/missing", headers=api_headers)
    assert response.status_code == 404


def test_api_memories(
    client: TestClient,
    api_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("recall.engines.hmem.hmem.reset", lambda: None)
    monkeypatch.setattr(
        "recall.engines.hmem.retrieve",
        lambda query, entity_ids=None, **_k: {
            "memories": [
                {"id": "m1", "content": "Seed round", "score": 0.9, "why": "gmail"},
            ],
            "strategy": "hybrid",
        },
    )
    response = client.get("/api/memories/karim-haddad", headers=api_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["memories"][0]["content"] == "Seed round"


def test_api_ask_clarification_shape(
    client: TestClient,
    api_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _done_state(
        status="needs_clarification",
        entity_id=None,
        entity_name=None,
        final_brief=None,
        question="Karim Haddad or Karim Saleh?",
        candidates=[
            Candidate(entity_id="karim-haddad", name="Karim Haddad", score=0.9, reason="name"),
            Candidate(entity_id="karim-saleh", name="Karim Saleh", score=0.85, reason="name"),
        ],
    )
    monkeypatch.setattr("recall.orchestrator.run", AsyncMock(return_value=state))
    response = client.post(
        "/api/ask",
        headers=api_headers,
        json={"text": "I'm meeting Karim"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "needs_clarification"
    assert len(body["candidates"]) == 2
    assert body["question"]
    assert body["speech"]
