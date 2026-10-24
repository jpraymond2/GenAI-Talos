"""World store: people, events, and the context built around them.

Orchestrator-facing facade::

    from recall.engines import world
    world.resolve("Ada Lovelace")
    world.person_context(entity_id)
"""

from __future__ import annotations

from typing import Any

from recall.config import Settings, get_settings
from recall.engines.world.context_builder import ContextBuilderService
from recall.engines.world.identity_service import IdentityService
from recall.engines.world.ingest import IngestPipelineService, run_world_ingest
from recall.engines.world.models import ResolveCandidate
from recall.engines.world.store import WorldStore, world_db_path
from recall.tracing import Tracer


class _WorldFacade:
    """Lazy facade so callers can use ``world.resolve`` / ``world.person_context``."""

    def __init__(self) -> None:
        self._store: WorldStore | None = None
        self._settings: Settings | None = None

    def _services(
        self,
        settings: Settings | None = None,
        tracer: Tracer | None = None,
    ) -> tuple[WorldStore, ContextBuilderService]:
        active = settings if settings is not None else get_settings()
        if self._store is None or self._settings is not active:
            self._settings = active
            self._store = WorldStore(path=world_db_path(active), settings=active)
        builder = ContextBuilderService(
            store=self._store,
            settings=active,
            tracer=tracer if tracer is not None else Tracer.current(),
        )
        return self._store, builder

    def resolve(self, name: str) -> list[ResolveCandidate]:
        """Ranked entity candidates for a spoken/typed name."""
        _, builder = self._services()
        return builder.resolve(name)

    def person_context(self, entity_id: str, budget_tokens: int = 1500) -> dict[str, Any]:
        """Structured person brief for the orchestrator."""
        _, builder = self._services()
        return builder.person_context(entity_id, budget_tokens=budget_tokens)

    def reset(self) -> None:
        """Drop cached store (tests that rebuild the DB)."""
        if self._store is not None:
            self._store.close()
        self._store = None
        self._settings = None


world = _WorldFacade()

__all__ = [
    "ContextBuilderService",
    "IdentityService",
    "IngestPipelineService",
    "WorldStore",
    "person_context",
    "resolve",
    "run_world_ingest",
    "world",
    "world_db_path",
]


def resolve(name: str) -> list[ResolveCandidate]:
    """Module-level alias for ``world.resolve``."""
    return world.resolve(name)


def person_context(entity_id: str, budget_tokens: int = 1500) -> dict[str, Any]:
    """Module-level alias for ``world.person_context``."""
    return world.person_context(entity_id, budget_tokens=budget_tokens)
