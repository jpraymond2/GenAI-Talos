"""Relationship labels: ingest classification, override, brief style."""

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
from recall.engines.world.relationship import (
    classify_relationship,
    set_relationship_override,
)
from recall.engines.world.store import WorldStore
from recall.llm.generator import reset_client
from recall.orchestrator import build_deps, run
from recall.schemas import Brief, CriticVerdict, Intent, PointVerdict, TalkingPoint
from recall.tracing import Tracer


@pytest.fixture
def world_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[WorldStore, Tracer]]:
    logs = tmp_path / "logs"
    world_db = tmp_path / "world_sample.db"
    monkeypatch.setenv("SAMPLE_MODE", "true")
    monkeypatch.setenv("DATA_BACKEND", "duckdb")
    monkeypatch.setenv("LOGS_DIR", str(logs))
    monkeypatch.setenv("NVIDIA_API_KEY", "")
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
    yield store, tracer
    store.close()
    world.reset()
    DuckDBBackend.clear_connection_cache()
    clear_default_query_layer()
    get_settings.cache_clear()
    reset_client()


def _entity_id(store: WorldStore, name: str) -> str:
    people = [e for e in store.list_entities(type="person") if e.canonical_name == name]
    assert people, f"missing entity {name}"
    return people[0].id


def test_karim_haddad_mixed_or_social(world_env: tuple[WorldStore, Tracer]) -> None:
    store, tracer = world_env
    eid = _entity_id(store, "Karim Haddad")
    assessment = store.latest_relationship_assessment(eid)
    assert assessment is not None
    assert assessment.label in {"mixed", "social"}
    assert assessment.confidence >= 0.55
    _ = tracer


def test_lina_chen_professional(world_env: tuple[WorldStore, Tracer]) -> None:
    store, _tracer = world_env
    eid = _entity_id(store, "Lina Chen")
    assessment = store.latest_relationship_assessment(eid)
    assert assessment is not None
    assert assessment.label == "professional"


def test_family_member_family(world_env: tuple[WorldStore, Tracer]) -> None:
    store, _tracer = world_env
    eid = _entity_id(store, "Maya Martin")
    assessment = store.latest_relationship_assessment(eid)
    assert assessment is not None
    assert assessment.label == "family"


def test_user_override_sticks_after_reassessment(
    world_env: tuple[WorldStore, Tracer],
) -> None:
    store, tracer = world_env
    eid = _entity_id(store, "Karim Haddad")
    set_relationship_override(
        eid, "professional", store=store, settings=get_settings(), run_id="test-override"
    )
    again = classify_relationship(
        eid,
        store=store,
        settings=get_settings(),
        query_layer=get_query_layer(get_settings(), tracer=tracer),
        tracer=tracer,
        run_id="test-reassess",
        force=True,
    )
    assert again.status == "user_override"
    assert again.label == "professional"


def test_professional_brief_style_for_lina(
    world_env: tuple[WorldStore, Tracer], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _tracer = world_env
    logs = tmp_path / "orch_logs"
    monkeypatch.setenv("LOGS_DIR", str(logs))
    get_settings.cache_clear()

    captured: dict[str, Any] = {}

    async def handler(
        prompt: str, system: str | None, schema: type[BaseModel], **kwargs: Any
    ):
        if schema is Intent:
            return Intent(
                kind="brief",
                person_name="Lina Chen",
                when=None,
                action_request=None,
                sources_plan=["gmail"],
                confidence=0.95,
            )
        if schema is Brief:
            captured["prompt"] = prompt
            return Brief(
                person="Lina Chen",
                headline="Lina Chen, Associate at Northbridge; last email this week.",
                points=[
                    TalkingPoint(
                        text="Follow up on the internship overview she sent.",
                        evidence_ids=["e1"],
                        kind="open_loop",
                        priority=1,
                    )
                ],
                caveats=[],
            )
        if schema is CriticVerdict:
            return CriticVerdict(
                overall="pass",
                verdicts=[
                    PointVerdict(index=0, decision="keep", reason="ok", fix_hint=None)
                ],
                missing=[],
                refetch=[],
            )
        raise AssertionError(schema)

    async def generate_json(
        prompt: str, system: str | None, schema: type[BaseModel], **kwargs: Any
    ):
        return await handler(prompt, system, schema, **kwargs)

    async def generate(*args: Any, **kwargs: Any) -> str:
        return ""

    # Point build_deps at the ingested temp world DB.
    monkeypatch.setenv("SAMPLE_MODE", "true")
    get_settings.cache_clear()
    deps = build_deps(
        sample_mode=True,
        logs_dir=logs,
        generate=generate,
        generate_json=generate_json,
    )
    # Swap world store to the fixture DB that already has assessments.
    deps.world_store = store
    from recall.engines.world.context_builder import ContextBuilderService

    deps.world = ContextBuilderService(
        store=store,
        settings=deps.settings,
        query_layer=deps.query_layer,
        tracer=deps.tracer,
    )

    state = asyncio.run(run("I'm meeting Lina Chen", deps=deps))
    assert state.status == "done"
    assert state.context.get("relationship_label") == "professional"
    assert "personal-life" in (state.context.get("relationship_style") or "").lower()
    assert "RELATIONSHIP: professional" in captured.get("prompt", "")
    # Brief itself should not mention personal hobbies in our mocked output.
    assert state.final_brief is not None
    joined = " ".join(p.text for p in state.final_brief.points).lower()
    assert "birthday" not in joined
    assert "habs" not in joined
