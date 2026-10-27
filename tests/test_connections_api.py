"""Connections status, Google sign-in guard rails, and the upcoming-meetings calendar."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

TOKEN = "test-token-123"
DEVICE = "device-token-456"


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("RECALL_API_TOKEN", TOKEN)
    monkeypatch.setenv("DEVICE_UPLOAD_TOKEN", DEVICE)
    monkeypatch.setenv("SAMPLE_MODE", "true")
    monkeypatch.setenv("DATA_BACKEND", "duckdb")
    monkeypatch.setenv("LOGS_DIR", str(tmp_path / "logs"))
    from recall.config import get_settings

    get_settings.cache_clear()
    from recall.connectors import google_auth
    from recall.data.duckdb_backend import DuckDBBackend
    from recall.data.query_layer import get_query_layer
    from recall.engines.world import store as store_mod
    from recall.engines.world.ingest import IngestPipelineService
    from recall.engines.world.store import WorldStore
    from recall.server import workspace

    db = tmp_path / "world.db"
    monkeypatch.setattr(store_mod, "world_db_path", lambda settings=None: db)
    monkeypatch.setattr(workspace, "_JOBS_DB", tmp_path / "jobs.db")
    monkeypatch.setattr(google_auth, "credentials_path", lambda settings=None: tmp_path / "secrets" / "credentials.json")
    monkeypatch.setattr(google_auth, "token_path", lambda settings=None: tmp_path / "secrets" / "token.json")
    DuckDBBackend.clear_connection_cache()
    settings = get_settings()
    st = WorldStore(path=db, settings=settings)
    IngestPipelineService(store=st, settings=settings).run(get_query_layer(settings))
    st.close()

    from recall.server.app import app

    return TestClient(app)


def auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


def test_connections_lists_every_source(client: TestClient) -> None:
    assert client.get("/api/connections").status_code == 401
    body = client.get("/api/connections", headers=auth()).json()
    ids = [s["id"] for s in body["sources"]]
    assert ids == ["imessage", "whatsapp", "contacts", "gmail", "calendar"]
    assert body["google"] == {"configured": False, "connected": False}
    gmail = next(s for s in body["sources"] if s["id"] == "gmail")
    assert gmail["action"] in ("connect", "reconnect")
    # A Mac check-in flips the Mac sources from "connect your Mac" to pullable.
    client.get("/api/mac/agent/next", params={"device_id": "mac-1"}, headers={"Authorization": f"Bearer {DEVICE}"})
    after = client.get("/api/connections", headers=auth()).json()
    assert after["mac"]["online"] is True
    assert all(s["action"] == "pull" for s in after["sources"] if s["via"] == "mac")


def test_google_start_explains_missing_setup(client: TestClient) -> None:
    r = client.get("/api/connect/google/start", headers=auth())
    assert r.status_code == 503 and "credentials.json" in r.json()["detail"]
    # Unknown / replayed state never writes a token.
    cb = client.get("/api/connect/google/callback", params={"state": "nope", "code": "x"}, follow_redirects=False)
    assert cb.status_code in (302, 307) and "connect_error" in cb.headers["location"]
    assert client.delete("/api/connections/google", headers=auth()).json() == {"ok": True}


def test_calendar_upcoming(client: TestClient) -> None:
    body = client.get("/api/calendar/upcoming", params={"days": 60}, headers=auth()).json()
    assert "now" in body and isinstance(body["events"], list)
    starts = [e["start"] for e in body["events"]]
    assert starts == sorted(starts)
    for e in body["events"]:
        assert e["end"] >= body["now"]
        for a in e["attendees"]:
            assert set(a) == {"email", "entity_id", "name"}
