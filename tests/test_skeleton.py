"""Skeleton checks: ask clarifies then continues, health responds, sample backends agree."""

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from recall.__main__ import app as cli
from recall.config import get_settings
from recall.data.query_layer import get_query_layer
from recall.server.app import app as api

runner = CliRunner()


@pytest.fixture(autouse=True)
def _clear_settings() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_ask_ambiguous_karim_then_select_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logs = tmp_path / "logs"
    monkeypatch.setenv("LOGS_DIR", str(logs))
    monkeypatch.setenv("SAMPLE_MODE", "true")
    monkeypatch.setenv("CACHE_DIR", str(Path("data/cache").resolve()))
    get_settings.cache_clear()

    result = runner.invoke(
        cli,
        ["ask", "I'm meeting Karim", "--sample"],
        input="1\n",
    )

    assert result.exit_code == 0, result.stdout + result.stderr
    assert "Karim Haddad or Karim Saleh?" in result.stdout
    assert "1. Karim Haddad" in result.stdout
    assert "resolved: Karim Haddad (karim-haddad)" in result.stdout
    assert "headline:" in result.stdout
    runs = list(logs.glob("*/run.json"))
    assert len(runs) >= 1
    # Last completed run should be the resolved brief.
    payloads = [json.loads(path.read_text(encoding="utf-8")) for path in runs]
    assert any(p.get("status") == "done" and p.get("entity_name") == "Karim Haddad" for p in payloads)


def test_ask_eof_leaves_clarification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logs = tmp_path / "logs"
    monkeypatch.setenv("LOGS_DIR", str(logs))
    monkeypatch.setenv("SAMPLE_MODE", "true")
    monkeypatch.setenv("CACHE_DIR", str(Path("data/cache").resolve()))
    get_settings.cache_clear()

    result = runner.invoke(cli, ["ask", "I'm meeting Karim", "--sample"], input="")

    assert result.exit_code == 0, result.stdout + result.stderr
    assert "needs_clarification" in result.stdout
    assert "headline:" not in result.stdout


def test_health() -> None:
    response = TestClient(api).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_trace_socket_accepts() -> None:
    client = TestClient(api)
    with client.websocket_connect("/ws/trace") as websocket:
        websocket.close()


@pytest.mark.parametrize("backend", ["duckdb", "zetaris"])
def test_sample_query_returns_no_rows(monkeypatch: pytest.MonkeyPatch, backend: str) -> None:
    monkeypatch.setenv("SAMPLE_MODE", "true")
    monkeypatch.setenv("DATA_BACKEND", backend)
    get_settings.cache_clear()
    assert get_query_layer().query("select 1") == []
