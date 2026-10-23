"""World model ingest + context checks against sample data only."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from recall.config import get_settings
from recall.data.duckdb_backend import DuckDBBackend
from recall.data.query_layer import clear_default_query_layer, get_query_layer
from recall.engines.world import world
from recall.engines.world.ingest import IngestPipelineService
from recall.engines.world.store import WorldStore
from recall.tracing import Tracer


@pytest.fixture
def world_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Path, Tracer, WorldStore]]:
    logs = tmp_path / "logs"
    db_path = tmp_path / "world_sample.db"
    monkeypatch.setenv("SAMPLE_MODE", "true")
    monkeypatch.setenv("DATA_BACKEND", "duckdb")
    monkeypatch.setenv("LOGS_DIR", str(logs))
    get_settings.cache_clear()
    clear_default_query_layer()
    DuckDBBackend.clear_connection_cache()
    world.reset()

    tracer = Tracer(logs_dir=logs)
    store = WorldStore(path=db_path)
    with tracer.activate():
        layer = get_query_layer(get_settings(), tracer=tracer)
        IngestPipelineService(store=store, settings=get_settings(), tracer=tracer).run(layer)

    yield db_path, tracer, store

    store.close()
    world.reset()
    DuckDBBackend.clear_connection_cache()
    clear_default_query_layer()
    get_settings.cache_clear()


def test_resolve_karim_two_candidates_close_scores(world_env: tuple[Path, Tracer, WorldStore]) -> None:
    db_path, tracer, store = world_env
    world.reset()
    # Point facade at the temp DB via SAMPLE_MODE path override: reopen store through monkeypatched path.
    # Use ContextBuilder directly on the test store.
    from recall.engines.world.context_builder import ContextBuilderService

    builder = ContextBuilderService(store=store, settings=get_settings(), tracer=tracer)
    candidates = builder.resolve("Karim")
    names = {c.name for c in candidates}
    assert "Karim Haddad" in names
    assert "Karim Saleh" in names
    assert len(candidates) >= 2
    scores = [c.score for c in candidates if c.name in {"Karim Haddad", "Karim Saleh"}]
    assert max(scores) - min(scores) < 0.08, "scores must be close enough that the caller must ask"
    # Do not assume Haddad is first when scores are essentially tied.
    top_two = sorted(
        [c for c in candidates if c.name in {"Karim Haddad", "Karim Saleh"}],
        key=lambda c: (-c.score, c.name),
    )
    assert abs(top_two[0].score - top_two[1].score) < 0.08


def test_karim_haddad_merged_aliases(world_env: tuple[Path, Tracer, WorldStore]) -> None:
    _, _, store = world_env
    people = [e for e in store.list_entities(type="person") if e.canonical_name == "Karim Haddad"]
    assert len(people) == 1
    entity = people[0]
    aliases = set(entity.aliases)
    assert "Karim 🇱🇧" in aliases or any("Karim" in a and "🇱🇧" in a for a in aliases)
    assert "karim@ledgerly.io" in aliases
    assert "+15145550142" in aliases
    assert any("@s.whatsapp.net" in a for a in aliases)


def test_one_city_conflict_montreal_toronto(world_env: tuple[Path, Tracer, WorldStore]) -> None:
    _, _, store = world_env
    haddad = next(e for e in store.list_entities(type="person") if e.canonical_name == "Karim Haddad")
    conflicts = [c for c in store.list_conflicts(haddad.id) if c.field == "city"]
    assert len(conflicts) == 1
    values = {item.value: item.source_ref for item in conflicts[0].values}
    assert "Montreal" in values
    assert "Toronto" in values
    assert values["Montreal"]
    assert values["Toronto"]
    assert values["Montreal"] != values["Toronto"]


def test_karim_haddad_one_conflict_and_title_founder(
    world_env: tuple[Path, Tracer, WorldStore],
) -> None:
    """Signature parse must yield title=Founder; only city may conflict."""
    _, _, store = world_env
    haddad = next(e for e in store.list_entities(type="person") if e.canonical_name == "Karim Haddad")
    assert haddad.attributes.get("title") == "Founder"
    assert "\n" not in haddad.attributes.get("title", "")
    conflicts = store.list_conflicts(haddad.id)
    assert len(conflicts) == 1
    assert conflicts[0].field == "city"


def test_open_loop_lina_intro(world_env: tuple[Path, Tracer, WorldStore]) -> None:
    _, tracer, store = world_env
    from recall.engines.world.context_builder import ContextBuilderService

    haddad = next(e for e in store.list_entities(type="person") if e.canonical_name == "Karim Haddad")
    builder = ContextBuilderService(store=store, settings=get_settings(), tracer=tracer)
    ctx = builder.person_context(haddad.id)
    assert ctx["open_loops"]
    assert any(
        "Lina Chen" in loop.get("text", "") or "Lina Chen" in loop.get("subject", "")
        for loop in ctx["open_loops"]
    )


def test_june_coffee_in_past_events(world_env: tuple[Path, Tracer, WorldStore]) -> None:
    _, tracer, store = world_env
    from recall.engines.world.context_builder import ContextBuilderService

    haddad = next(e for e in store.list_entities(type="person") if e.canonical_name == "Karim Haddad")
    builder = ContextBuilderService(store=store, settings=get_settings(), tracer=tracer)
    ctx = builder.person_context(haddad.id)
    titles = [e["title"] for e in ctx["past_events"]]
    assert "Coffee w/ Karim" in titles


def test_rerun_ingest_stable_ids_no_duplicates(
    world_env: tuple[Path, Tracer, WorldStore], tmp_path: Path
) -> None:
    db_path, tracer, store = world_env
    before = {e.canonical_name: e.id for e in store.list_entities(type="person")}
    assert "Karim Haddad" in before

    layer = get_query_layer(get_settings(), tracer=tracer)
    with tracer.activate():
        IngestPipelineService(store=store, settings=get_settings(), tracer=tracer).run(layer)

    after_people = store.list_entities(type="person")
    after = {e.canonical_name: e.id for e in after_people}
    assert after["Karim Haddad"] == before["Karim Haddad"]
    assert after["Karim Saleh"] == before["Karim Saleh"]
    # No duplicate Haddad rows
    assert sum(1 for e in after_people if e.canonical_name == "Karim Haddad") == 1


def test_world_provenance_jsonl_written(world_env: tuple[Path, Tracer, WorldStore]) -> None:
    _, tracer, _ = world_env
    provenance = Path(tracer.path).parent / "world_provenance.jsonl"
    assert provenance.is_file()
    lines = [ln for ln in provenance.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert lines
    record = json.loads(lines[0])
    assert "entity_id" in record
    assert "field" in record
    assert "source_ref" in record
    assert record.get("run_id") == tracer.run_id
