"""Identity scenario bench: all fixtures must pass."""

from __future__ import annotations

from pathlib import Path

import pytest

from recall.config import get_settings
from recall.data.duckdb_backend import DuckDBBackend
from recall.engines.world import world
from recall.engines.world.identity_bench import format_bench_table, run_bench
from recall.llm.generator import reset_client


def test_identity_bench_all_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run the full identity scenario bench and require 10/10."""
    monkeypatch.setenv("SAMPLE_MODE", "true")
    monkeypatch.setenv("NVIDIA_API_KEY", "")
    monkeypatch.setenv("LOGS_DIR", str(tmp_path / "logs"))
    get_settings.cache_clear()
    reset_client()
    world.reset()
    DuckDBBackend.clear_connection_cache()

    work = tmp_path / "bench_work"
    payload = run_bench(work_root=work)
    print(format_bench_table(payload))
    assert payload["total"] == 10, payload
    assert payload["passed"] == payload["total"], payload
    assert int(payload["scoring_version"]) >= 3

    world.reset()
    DuckDBBackend.clear_connection_cache()
    get_settings.cache_clear()
    reset_client()
