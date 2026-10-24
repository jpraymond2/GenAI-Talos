"""H-MEM engine tests — sample mode only, no network."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from recall.config import get_settings
from recall.data.duckdb_backend import DuckDBBackend
from recall.data.query_layer import clear_default_query_layer, get_query_layer
from recall.engines import hmem as hmem_mod
from recall.engines.hmem.ledger import TrustLedgerService
from recall.engines.hmem.mining import MemoryMiningService
from recall.engines.hmem.retrieval import MemoryRetrievalService
from recall.engines.hmem.store import MemoryStoreService
from recall.engines.world import world
from recall.engines.world.ingest import IngestPipelineService
from recall.engines.world.store import WorldStore
from recall.tracing import Tracer


@pytest.fixture
def hmem_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[WorldStore, MemoryStoreService, TrustLedgerService, Tracer]]:
    logs = tmp_path / "logs"
    world_db = tmp_path / "world_sample.db"
    hmem_db = tmp_path / "hmem_sample.db"
    monkeypatch.setenv("SAMPLE_MODE", "true")
    monkeypatch.setenv("DATA_BACKEND", "duckdb")
    monkeypatch.setenv("LOGS_DIR", str(logs))
    get_settings.cache_clear()
    clear_default_query_layer()
    DuckDBBackend.clear_connection_cache()
    world.reset()
    hmem_mod.hmem.reset()

    tracer = Tracer(logs_dir=logs)
    wstore = WorldStore(path=world_db)
    with tracer.activate():
        layer = get_query_layer(get_settings(), tracer=tracer)
        IngestPipelineService(store=wstore, settings=get_settings(), tracer=tracer).run(layer)

    world.reset()
    world._store = wstore  # noqa: SLF001 — test wiring
    world._settings = get_settings()  # noqa: SLF001

    store = MemoryStoreService(path=hmem_db, settings=get_settings())
    ledger = TrustLedgerService(settings=get_settings(), tracer=tracer, run_id=tracer.run_id)

    yield wstore, store, ledger, tracer

    store.close()
    wstore.close()
    world.reset()
    hmem_mod.hmem.reset()
    DuckDBBackend.clear_connection_cache()
    clear_default_query_layer()
    get_settings.cache_clear()


def _haddad_id(wstore: WorldStore) -> str:
    people = [e for e in wstore.list_entities(type="person") if e.canonical_name == "Karim Haddad"]
    assert len(people) == 1
    return people[0].id


def _mine(
    store: MemoryStoreService,
    ledger: TrustLedgerService,
    tracer: Tracer,
    wstore: WorldStore,
    text: str,
):
    miner = MemoryMiningService(
        store=store,
        ledger=ledger,
        settings=get_settings(),
        tracer=tracer,
        world_store=wstore,
    )
    return asyncio.run(miner.mine({"kind": "user_note", "text": text}))


def test_note_splits_into_two_memories_linked_to_haddad(
    hmem_env: tuple[WorldStore, MemoryStoreService, TrustLedgerService, Tracer],
) -> None:
    wstore, store, ledger, tracer = hmem_env
    haddad_id = _haddad_id(wstore)
    created = _mine(
        store,
        ledger,
        tracer,
        wstore,
        "Karim Haddad moves to Toronto in January and wants an intro to Lina",
    )
    assert len(created) == 2
    for mem in created:
        assert haddad_id in mem.entity_ids


def test_retrieve_meeting_karim_returns_two_first(
    hmem_env: tuple[WorldStore, MemoryStoreService, TrustLedgerService, Tracer],
) -> None:
    wstore, store, ledger, tracer = hmem_env
    haddad_id = _haddad_id(wstore)
    _mine(
        store,
        ledger,
        tracer,
        wstore,
        "Karim Haddad moves to Toronto in January and wants an intro to Lina",
    )
    retrieval = MemoryRetrievalService(store=store, settings=get_settings(), tracer=tracer)
    result = asyncio.run(
        retrieval.retrieve("meeting Karim", [haddad_id], top_n=5, threshold=0.1)
    )
    hits = result["memories"]
    assert len(hits) >= 2
    contents = " ".join(h["content"].lower() for h in hits[:2])
    assert "toronto" in contents
    assert "intro" in contents or "lina" in contents
    for hit in hits[:2]:
        assert hit["score"] > 0
        assert hit["why"]


def test_montreal_note_supersedes_toronto_and_logs_conflict(
    hmem_env: tuple[WorldStore, MemoryStoreService, TrustLedgerService, Tracer],
) -> None:
    wstore, store, ledger, tracer = hmem_env
    first = _mine(
        store,
        ledger,
        tracer,
        wstore,
        "Karim Haddad moves to Toronto in January and wants an intro to Lina",
    )
    toronto = next(m for m in first if "Toronto" in m.content)
    _mine(store, ledger, tracer, wstore, "Karim Haddad is staying in Montreal")

    row = store._conn.execute(  # noqa: SLF001
        "SELECT superseded_by FROM memories WHERE id = ?", (toronto.id,)
    ).fetchone()
    assert row is not None and row["superseded_by"]

    ledger_text = ledger.path.read_text(encoding="utf-8")
    assert "conflict_detected" in ledger_text
    assert "conflict_resolved" in ledger_text


def test_promote_on_remember_and_access_count(
    hmem_env: tuple[WorldStore, MemoryStoreService, TrustLedgerService, Tracer],
) -> None:
    wstore, store, ledger, tracer = hmem_env
    remembered = _mine(
        store, ledger, tracer, wstore, "remember Karim Haddad prefers morning meetings"
    )
    assert remembered
    assert remembered[0].layer == "long_term"

    short = _mine(store, ledger, tracer, wstore, "Karim likes espresso")
    assert short and short[0].layer == "short_term"
    mid = short[0]
    for _ in range(3):
        store.bump_access(mid.id)
    promoted = store.get(mid.id)
    assert promoted is not None
    assert promoted.layer == "long_term"
    assert promoted.access_count >= 3


def test_decay_lowers_short_term_only(
    hmem_env: tuple[WorldStore, MemoryStoreService, TrustLedgerService, Tracer],
) -> None:
    wstore, store, ledger, tracer = hmem_env
    long_mem = _mine(
        store, ledger, tracer, wstore, "remember Karim Haddad prefers morning meetings"
    )[0]
    short = _mine(store, ledger, tracer, wstore, "Karim likes espresso")[0]
    old = (datetime.now(timezone.utc) - timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    short.timestamp = old
    short.last_accessed = old
    short.confidence = 1.0
    store.update(short)
    long_before = store.get(long_mem.id)
    assert long_before is not None
    long_conf = long_before.confidence

    store.decay(as_of=datetime.now(timezone.utc))
    short_after = store.get(short.id)
    long_after = store.get(long_mem.id)
    assert short_after is not None and short_after.confidence < 1.0
    assert long_after is not None and long_after.confidence == long_conf


def test_feedback_helpful_and_wrong(
    hmem_env: tuple[WorldStore, MemoryStoreService, TrustLedgerService, Tracer],
) -> None:
    wstore, store, ledger, tracer = hmem_env
    created = _mine(store, ledger, tracer, wstore, "Karim likes espresso")
    mem = created[0]
    before = mem.confidence

    hmem_mod.hmem.reset()
    hmem_mod.hmem._store = store  # noqa: SLF001
    hmem_mod.hmem._ledger = ledger  # noqa: SLF001
    hmem_mod.hmem._settings = get_settings()  # noqa: SLF001

    helpful = hmem_mod.hmem.feedback(mem.id, "helpful")
    assert helpful is not None
    assert helpful.confidence == pytest.approx(before + 0.05)
    assert helpful.access_count >= 1

    wrong = hmem_mod.hmem.feedback(mem.id, "wrong")
    assert wrong is not None
    assert wrong.confidence == pytest.approx(helpful.confidence - 0.2)
    assert "review" in wrong.tags


def test_memory_ledger_written_with_before_after(
    hmem_env: tuple[WorldStore, MemoryStoreService, TrustLedgerService, Tracer],
) -> None:
    wstore, store, ledger, tracer = hmem_env
    _mine(
        store,
        ledger,
        tracer,
        wstore,
        "Karim Haddad moves to Toronto in January and wants an intro to Lina",
    )
    assert ledger.path.is_file()
    lines = [json.loads(ln) for ln in ledger.path.read_text(encoding="utf-8").splitlines() if ln]
    assert lines
    creates = [ln for ln in lines if ln["action"] == "create"]
    assert creates
    assert creates[0]["before"] is None
    assert creates[0]["after"] is not None
    assert "content" in creates[0]["after"]


def test_cli_note_and_memories_reject_empty_world(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Missing/empty world DB must instruct ingest — not invent candidates or an empty DB."""
    from typer.testing import CliRunner

    from recall.__main__ import _WORLD_EMPTY_MSG, app as cli
    from recall.engines.world import world
    from recall.engines.world.store import WorldStore, world_db_path

    logs = tmp_path / "logs"
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SAMPLE_MODE", "true")
    monkeypatch.setenv("LOGS_DIR", str(logs))
    get_settings.cache_clear()
    clear_default_query_layer()
    world.reset()
    hmem_mod.hmem.reset()

    runner = CliRunner()

    # Case 1: world DB file missing
    assert not world_db_path(get_settings()).is_file()
    missing_note = runner.invoke(cli, ["note", "Karim likes espresso", "--sample"])
    assert missing_note.exit_code == 1
    assert _WORLD_EMPTY_MSG in missing_note.stdout
    assert not world_db_path(get_settings()).is_file(), "note must not create an empty world DB"

    missing_memories = runner.invoke(cli, ["memories", "Karim", "--sample"])
    assert missing_memories.exit_code == 1
    assert _WORLD_EMPTY_MSG in missing_memories.stdout
    assert "No candidates" not in missing_memories.stdout

    # Case 2: world DB exists but has zero entities
    empty = WorldStore(path=world_db_path(get_settings()))
    assert empty.count_entities() == 0
    empty.close()
    empty_note = runner.invoke(cli, ["note", "Karim likes espresso", "--sample"])
    assert empty_note.exit_code == 1
    assert _WORLD_EMPTY_MSG in empty_note.stdout

    empty_memories = runner.invoke(cli, ["memories", "Karim", "--sample"])
    assert empty_memories.exit_code == 1
    assert _WORLD_EMPTY_MSG in empty_memories.stdout
    assert "No candidates" not in empty_memories.stdout

    get_settings.cache_clear()
    world.reset()
    hmem_mod.hmem.reset()
