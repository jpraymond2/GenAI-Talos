"""Skeleton checks: ask clarifies then continues, health responds, sample backends agree."""
import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from recall.__main__ import app as cli
from recall.config import DataBackend, get_settings
from recall.data.duckdb_backend import DuckDBBackend
from recall.data.query_layer import clear_default_query_layer, get_query_layer
from recall.server.app import app as api
from recall.tracing import Tracer

runner = CliRunner()


@pytest.fixture(autouse=True)
def _clear_settings() -> Iterator[None]:
    get_settings.cache_clear()
    clear_default_query_layer()
    DuckDBBackend.clear_connection_cache()
    yield
    DuckDBBackend.clear_connection_cache()
    clear_default_query_layer()
    get_settings.cache_clear()


def test_ask_prints_ok_and_writes_a_trace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    logs = tmp_path / "logs"
    monkeypatch.setenv("LOGS_DIR", str(logs))
    monkeypatch.setenv("SAMPLE_MODE", "true")
    get_settings.cache_clear()

    result = runner.invoke(cli, ["ask", "I'm meeting Karim", "--sample"])

    assert result.exit_code == 0
    assert "Recall skeleton OK" in result.stdout
    traces = list(logs.glob("*/trace.jsonl"))
    assert len(traces) == 1
    record = json.loads(traces[0].read_text(encoding="utf-8").strip())
    assert record["agent"] == "cli"
    assert record["kind"] == "ask"
    assert record["payload"]["utterance"] == "I'm meeting Karim"
    assert record["payload"]["sample_mode"] is True


def test_health() -> None:
    response = TestClient(api).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_trace_socket_accepts() -> None:
    client = TestClient(api)
    with client.websocket_connect("/ws/trace") as websocket:
        websocket.close()


def test_sample_backends_return_same_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """In SAMPLE_MODE, DuckDB and Zetaris-with-fallback return the same rows."""
    logs = tmp_path / "logs"
    monkeypatch.setenv("LOGS_DIR", str(logs))
    monkeypatch.setenv("SAMPLE_MODE", "true")
    monkeypatch.delenv("ZETARIS_URL", raising=False)
    get_settings.cache_clear()

    sql = "SELECT handle_id, kind, value FROM recall.handles ORDER BY handle_id"
    tracer = Tracer(logs_dir=logs)

    monkeypatch.setenv("DATA_BACKEND", "duckdb")
    get_settings.cache_clear()
    duck_settings = get_settings()
    assert duck_settings.data_backend is DataBackend.DUCKDB
    duck_rows = get_query_layer(duck_settings, tracer=tracer).query(
        sql, source_tag="skeleton.duckdb"
    )

    monkeypatch.setenv("DATA_BACKEND", "zetaris")
    get_settings.cache_clear()
    zetaris_settings = get_settings()
    assert zetaris_settings.data_backend is DataBackend.ZETARIS
    zetaris_rows = get_query_layer(zetaris_settings, tracer=tracer).query(
        sql, source_tag="skeleton.zetaris"
    )

    assert duck_rows
    assert zetaris_rows == duck_rows
