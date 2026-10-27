"""Dashboard workspace API: login, contact cards + edits, and the Mac pull mailbox."""

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
    monkeypatch.setenv("RECALL_LOGIN_EMAIL", "demo@recall.app")
    monkeypatch.setenv("RECALL_LOGIN_PASSWORD", "letmein")
    monkeypatch.setenv("SAMPLE_MODE", "true")
    monkeypatch.setenv("DATA_BACKEND", "duckdb")
    monkeypatch.setenv("LOGS_DIR", str(tmp_path / "logs"))
    from recall.config import get_settings

    get_settings.cache_clear()

    # Build a throwaway world from data/sample (never touch data/world_sample.db)
    from recall.data.duckdb_backend import DuckDBBackend
    from recall.data.query_layer import get_query_layer
    from recall.engines.world import store as store_mod
    from recall.engines.world.ingest import IngestPipelineService
    from recall.engines.world.store import WorldStore
    from recall.server import workspace

    db = tmp_path / "world.db"
    monkeypatch.setattr(store_mod, "world_db_path", lambda settings=None: db)
    monkeypatch.setattr(workspace, "_JOBS_DB", tmp_path / "jobs.db")
    DuckDBBackend.clear_connection_cache()
    settings = get_settings()
    st = WorldStore(path=db, settings=settings)
    IngestPipelineService(store=st, settings=settings).run(get_query_layer(settings))
    st.close()

    from recall.server.app import app

    return TestClient(app)


def auth(token: str = TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_login(client: TestClient) -> None:
    assert client.post("/api/login", json={"email": "demo@recall.app", "password": "nope"}).status_code == 401
    ok = client.post("/api/login", json={"email": "demo@recall.app", "password": "letmein"})
    assert ok.status_code == 200 and ok.json()["token"] == TOKEN
    assert client.post("/api/login", json={"password": TOKEN}).status_code == 200


def test_people_cards_and_edits_survive(client: TestClient) -> None:
    people = client.get("/api/people", headers=auth()).json()["people"]
    assert people and all("entity_id" in p and "name" in p for p in people)
    target = next(p for p in people if p["phones"] or p["emails"])
    edited = client.patch(f"/api/people/{target['entity_id']}", headers=auth(),
                          json={"org": "Edited Org", "notes": "met at the demo"}).json()
    assert edited["org"] == "Edited Org" and "org" in edited["edited_fields"]
    assert client.get("/api/people", headers=auth()).status_code == 200
    assert client.get("/api/people", headers=auth("wrong")).status_code == 401


def test_mac_pull_mailbox(client: TestClient) -> None:
    dev = {"Authorization": f"Bearer {DEVICE}"}
    assert client.get("/api/mac/device", headers=auth()).json()["connected"] is False
    # Nothing queued yet: heartbeat only
    assert client.get("/api/mac/agent/next", params={"device_id": "mac-1"}, headers=dev).status_code == 204
    assert client.get("/api/mac/device", headers=auth()).json()["online"] is True

    pull = client.post("/api/mac/pulls", headers=auth(), json={"days": 30, "sources": ["imessage", "contacts"]}).json()["pull"]
    assert pull["status"] == "queued" and pull["params"]["days"] == 30

    job = client.get("/api/mac/agent/next", params={"device_id": "mac-1"}, headers=dev)
    assert job.status_code == 200 and job.json()["id"] == pull["id"]
    assert client.get("/api/mac/pulls/latest", headers=auth()).json()["pull"]["status"] == "running"

    r = client.post(f"/api/mac/agent/jobs/{pull['id']}", headers=dev, json={"status": "done", "progress": {"messages": 12}})
    assert r.status_code == 200
    status = client.get("/api/mac/pulls/latest", headers=auth()).json()["pull"]["status"]
    assert status in ("building", "ready")

    assert client.post("/api/mac/pulls", headers=auth(), json={"contacts": "selected"}).status_code == 400
    assert client.get("/api/mac/agent/next", params={"device_id": "x"}, headers=auth()).status_code == 401
