"""Sample-mode local token endpoint + live-mode still requires a token."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from recall.config import get_settings
from recall.schemas import RunState, Utterance
from recall.server import auth as auth_mod
from recall.server.app import app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app, base_url="http://127.0.0.1")


def test_local_token_sample_mode_loopback(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SAMPLE_MODE", "true")
    monkeypatch.setenv("RECALL_API_TOKEN", "")
    get_settings.cache_clear()
    auth_mod.reset_ephemeral_token_for_tests()

    res = client.get("/api/local-token")
    assert res.status_code == 200
    token = res.json()["token"]
    assert token

    denied = client.post("/api/ask", json={"text": "hi"})
    assert denied.status_code == 401

    fake = RunState(
        run_id="local-token-run",
        utterance=Utterance(
            text="hi", ts=datetime.now(timezone.utc), mode="text"
        ),
        status="done",
    )
    monkeypatch.setattr("recall.orchestrator.run", AsyncMock(return_value=fake))
    ok = client.post(
        "/api/ask",
        headers={"Authorization": f"Bearer {token}"},
        json={"text": "hi"},
    )
    assert ok.status_code == 200


def test_live_mode_requires_configured_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SAMPLE_MODE", "false")
    monkeypatch.setenv("RECALL_API_TOKEN", "")
    get_settings.cache_clear()
    auth_mod.reset_ephemeral_token_for_tests()
    client = TestClient(app, base_url="http://127.0.0.1")
    res = client.get("/api/local-token")
    assert res.status_code == 404
    ask = client.post("/api/ask", json={"text": "hi"})
    assert ask.status_code == 503


@pytest.mark.parametrize(
    "header,value",
    [
        ("X-Forwarded-For", "1.2.3.4"),
        ("X-Forwarded-Host", "example.ts.net"),
        ("Tailscale-User-Login", "user@example.com"),
    ],
)
def test_local_token_404_with_proxy_headers(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    header: str,
    value: str,
) -> None:
    monkeypatch.setenv("SAMPLE_MODE", "true")
    monkeypatch.setenv("RECALL_API_TOKEN", "")
    get_settings.cache_clear()
    auth_mod.reset_ephemeral_token_for_tests()
    res = client.get("/api/local-token", headers={header: value})
    assert res.status_code == 404
