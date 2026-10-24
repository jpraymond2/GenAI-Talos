"""On-demand preference profiles and suggest flow."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from recall.config import get_settings
from recall.data.duckdb_backend import DuckDBBackend
from recall.data.query_layer import clear_default_query_layer, get_query_layer
from recall.engines.world import world
from recall.engines.world.ingest import IngestPipelineService
from recall.engines.world.preferences import get_preferences, opt_out_preferences
from recall.engines.world.store import WorldStore
from recall.llm.generator import reset_client
from recall.orchestrator import build_deps, run
from recall.schemas import Intent
from recall.tracing import Tracer


@pytest.fixture
def world_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[WorldStore, Tracer, Path]]:
    logs = tmp_path / "logs"
    world_db = tmp_path / "world_sample.db"
    monkeypatch.setenv("SAMPLE_MODE", "true")
    monkeypatch.setenv("DATA_BACKEND", "duckdb")
    monkeypatch.setenv("LOGS_DIR", str(logs))
    monkeypatch.setenv("NVIDIA_API_KEY", "")
    monkeypatch.setenv("PREFERENCE_PROFILES", "true")
    monkeypatch.setenv("CACHE_DIR", str(Path("data/cache").resolve()))
    get_settings.cache_clear()
    clear_default_query_layer()
    DuckDBBackend.clear_connection_cache()
    world.reset()
    reset_client()

    tracer = Tracer(logs_dir=logs)
    store = WorldStore(path=world_db, settings=get_settings())
    with tracer.activate():
        layer = get_query_layer(get_settings(), tracer=tracer)
        IngestPipelineService(store=store, settings=get_settings(), tracer=tracer).run(
            layer
        )

    world.reset()
    world._store = store  # noqa: SLF001
    world._settings = get_settings()  # noqa: SLF001
    yield store, tracer, logs
    store.close()
    world.reset()
    DuckDBBackend.clear_connection_cache()
    clear_default_query_layer()
    get_settings.cache_clear()
    reset_client()


def _haddad_id(store: WorldStore) -> str:
    people = [
        e for e in store.list_entities(type="person") if e.canonical_name == "Karim Haddad"
    ]
    assert people
    return people[0].id


def test_no_preference_profile_after_ingest(
    world_env: tuple[WorldStore, Tracer, Path],
) -> None:
    store, _tracer, _logs = world_env
    eid = _haddad_id(store)
    assert store.get_preference_profile(eid) is None


def test_lunch_suggest_builds_profile_and_ranks_lebanese(
    world_env: tuple[WorldStore, Tracer, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _tracer, logs = world_env
    monkeypatch.setenv("LOGS_DIR", str(logs))
    get_settings.cache_clear()

    async def handler(
        prompt: str, system: str | None, schema: type[BaseModel], **kwargs: Any
    ):
        if schema is Intent:
            return Intent(
                kind="suggest",
                person_name="Karim Haddad",
                when="tomorrow",
                action_request="lunch",
                meal_type="lunch",
                sources_plan=["memory"],
                confidence=0.95,
            )
        raise AssertionError(schema)

    async def generate_json(
        prompt: str, system: str | None, schema: type[BaseModel], **kwargs: Any
    ):
        return await handler(prompt, system, schema, **kwargs)

    async def generate(*args: Any, **kwargs: Any) -> str:
        return ""

    deps = build_deps(
        sample_mode=True,
        logs_dir=logs,
        generate=generate,
        generate_json=generate_json,
    )
    deps.world_store = store
    from recall.engines.world.context_builder import ContextBuilderService

    deps.world = ContextBuilderService(
        store=store,
        settings=deps.settings,
        query_layer=deps.query_layer,
        tracer=deps.tracer,
    )

    state = asyncio.run(
        run("Where should I have lunch with Karim Haddad tomorrow?", deps=deps)
    )
    assert state.status == "done"
    eid = _haddad_id(store)
    profile = store.get_preference_profile(eid)
    assert profile is not None
    assert profile.items

    assert state.final_brief is not None
    texts = " ".join(
        [state.final_brief.headline, *[p.text for p in state.final_brief.points]]
    ).lower()
    assert "cedar" in texts or "vine" in texts or "manakish" in texts
    assert "sushi" not in texts

    # First ranked suggestion should prefer the praised Lebanese place.
    suggest = state.context.get("suggest_result") or {}
    suggestions = suggest.get("suggestions") or []
    assert suggestions
    top = suggestions[0]["place_or_cuisine"].lower()
    assert "cedar" in top or "vine" in top


def test_opt_out_deletes_and_blocks_rebuild(
    world_env: tuple[WorldStore, Tracer, Path],
) -> None:
    store, tracer, _logs = world_env
    eid = _haddad_id(store)
    layer = get_query_layer(get_settings(), tracer=tracer)
    get_preferences(
        eid,
        store=store,
        settings=get_settings(),
        query_layer=layer,
        tracer=tracer,
        reason="test",
    )
    assert store.get_preference_profile(eid) is not None
    opt_out_preferences(eid, store=store, settings=get_settings())
    tomb = store.get_preference_profile(eid)
    assert tomb is not None and tomb.opted_out
    blocked = get_preferences(
        eid,
        store=store,
        settings=get_settings(),
        query_layer=layer,
        tracer=tracer,
        reason="test_blocked",
        force=True,
    )
    assert blocked.opted_out
    assert blocked.items == []


def test_group_chat_diet_not_attributed_to_haddad(
    world_env: tuple[WorldStore, Tracer, Path],
) -> None:
    store, tracer, _logs = world_env
    eid = _haddad_id(store)
    layer = get_query_layer(get_settings(), tracer=tracer)
    profile = get_preferences(
        eid,
        store=store,
        settings=get_settings(),
        query_layer=layer,
        tracer=tracer,
        reason="test_diet",
    )
    diets = [i for i in profile.items if i.kind == "dietary_stated"]
    assert not diets, f"unexpected dietary items: {diets}"
    # Marcus's vegetarian line must not appear as Karim's preference.
    values = " ".join(i.value.lower() for i in profile.items)
    assert "vegetarian" not in values
