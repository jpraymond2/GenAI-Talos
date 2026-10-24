"""Shared pytest fixtures. Keep tests off live Zetaris from the developer's .env."""

from __future__ import annotations

import pytest

from recall.config import get_settings
from recall.data.query_layer import clear_default_query_layer


@pytest.fixture(autouse=True)
def _force_duckdb_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default every test to DuckDB; individual tests may still monkeypatch zetaris."""
    monkeypatch.setenv("DATA_BACKEND", "duckdb")
    get_settings.cache_clear()
    clear_default_query_layer()
    yield
    clear_default_query_layer()
    get_settings.cache_clear()
