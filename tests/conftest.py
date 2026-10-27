"""Shared pytest fixtures. Keep tests off live Zetaris from the developer's .env."""

from __future__ import annotations

import pytest

from recall.config import get_settings
from recall.data.duckdb_backend import DuckDBBackend
from recall.data.query_layer import clear_default_query_layer
from recall.data.zetaris_backend import clear_zetaris_circuit


@pytest.fixture(autouse=True)
def _force_duckdb_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default every test to DuckDB; individual tests may still monkeypatch zetaris."""
    monkeypatch.setenv("DATA_BACKEND", "duckdb")
    monkeypatch.setenv("SAMPLE_MODE", "true")
    monkeypatch.setenv("RECALL_API_TOKEN", "test-token-recall")
    # Pin unit-test fixture; demo default is data/synthetic/tables.
    monkeypatch.setenv("SAMPLE_DATA_DIR", "data/sample")
    get_settings.cache_clear()
    clear_default_query_layer()
    clear_zetaris_circuit()
    DuckDBBackend.clear_connection_cache()
    yield
    clear_default_query_layer()
    clear_zetaris_circuit()
    DuckDBBackend.clear_connection_cache()
    get_settings.cache_clear()


@pytest.fixture()
def api_headers() -> dict[str, str]:
    """Authorization header matching the autouse test token."""
    return {"Authorization": "Bearer test-token-recall"}
