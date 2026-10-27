"""'Skip sign-in' works on synthetic data and can be switched off."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_guest_login_in_sample_mode(monkeypatch) -> None:
    from recall.config import get_settings
    from recall.server.app import app

    client = TestClient(app)
    r = client.post("/api/login/guest")
    assert r.status_code == 200 and r.json()["token"] == "test-token-recall"
    assert client.get("/api/people", headers={"Authorization": f"Bearer {r.json()['token']}"}).status_code in (200, 404)

    monkeypatch.setenv("RECALL_ALLOW_GUEST", "false")
    get_settings.cache_clear()
    assert client.post("/api/login/guest").status_code == 403
