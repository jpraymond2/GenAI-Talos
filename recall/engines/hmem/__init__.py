"""Hierarchical memory: store, retrieval, mining, and the write ledger.

Orchestrator-facing facade (sync wrappers around async mine/retrieve)::

    from recall.engines import hmem
    hmem.mine({"kind": "user_note", "text": "..."})
    hmem.retrieve("meeting Ada", [entity_id])
    hmem.feedback(memory_id, "helpful")
"""

from __future__ import annotations

import asyncio
from typing import Any, Literal

from recall.config import Settings, get_settings
from recall.engines.hmem.ledger import TrustLedgerService
from recall.engines.hmem.mining import MemoryMiningService
from recall.engines.hmem.models import Memory, MineEvent
from recall.engines.hmem.retrieval import MemoryRetrievalService
from recall.engines.hmem.store import MemoryStoreService, hmem_db_path
from recall.tracing import Tracer


def _run_async(coro: Any) -> Any:
    """Run a coroutine from sync code (CLI / orchestrator)."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    # Already inside an event loop — run in a fresh loop in a thread is heavy;
    # for tests/CLI there is typically no running loop.
    return asyncio.run(coro)


class _HMemFacade:
    """Lazy facade wiring store, mining, retrieval, ledger, and feedback."""

    def __init__(self) -> None:
        self._store: MemoryStoreService | None = None
        self._settings: Settings | None = None
        self._ledger: TrustLedgerService | None = None

    def _wire(
        self,
        settings: Settings | None = None,
        tracer: Tracer | None = None,
    ) -> tuple[MemoryStoreService, TrustLedgerService, Tracer | None]:
        active = settings if settings is not None else get_settings()
        active_tracer = tracer if tracer is not None else Tracer.current()
        if self._store is None or self._settings is not active:
            self._settings = active
            if self._store is not None:
                self._store.close()
            self._store = MemoryStoreService(path=hmem_db_path(active), settings=active)
            self._ledger = TrustLedgerService(
                settings=active,
                tracer=active_tracer,
                run_id=active_tracer.run_id if active_tracer else None,
            )
        assert self._store is not None and self._ledger is not None
        return self._store, self._ledger, active_tracer

    def reset(self) -> None:
        if self._store is not None:
            self._store.close()
        self._store = None
        self._ledger = None
        self._settings = None

    def mine(self, event: MineEvent | dict[str, Any]) -> list[Memory]:
        """Mine memories from an event (sync wrapper)."""
        store, ledger, tracer = self._wire()
        service = MemoryMiningService(
            store=store, ledger=ledger, settings=self._settings, tracer=tracer
        )
        return _run_async(service.mine(event))

    def retrieve(
        self,
        query: str,
        entity_ids: list[str] | None = None,
        top_n: int = 5,
        threshold: float = 0.35,
    ) -> dict[str, Any]:
        """Hybrid retrieve (sync wrapper)."""
        store, ledger, tracer = self._wire()
        service = MemoryRetrievalService(
            store=store, settings=self._settings, tracer=tracer, ledger=ledger
        )
        return _run_async(
            service.retrieve(query, entity_ids=entity_ids, top_n=top_n, threshold=threshold)
        )

    def feedback(self, memory_id: str, verdict: Literal["helpful", "wrong"]) -> Memory | None:
        """Apply critic feedback: helpful (+0.05, +1 access) or wrong (-0.2, tag review)."""
        store, ledger, tracer = self._wire()
        mem = store.get(memory_id)
        if mem is None:
            return None
        before = mem.model_copy(deep=True)
        if verdict == "helpful":
            mem.confidence = min(1.0, mem.confidence + 0.05)
            mem.access_count += 1
            mem.last_accessed = __import__("datetime").datetime.now(
                __import__("datetime").timezone.utc
            ).strftime("%Y-%m-%dT%H:%M:%SZ")
        else:
            mem.confidence = max(0.0, mem.confidence - 0.2)
            if "review" not in mem.tags:
                mem.tags = [*mem.tags, "review"]
        store.update(mem)
        if verdict == "helpful" and mem.access_count >= 3:
            promoted = store.promote(mem.id, reason="access_count")
            if promoted is not None:
                mem = promoted
                ledger.record("promote", memory_id=mem.id, before=before, after=mem)
        ledger.record("feedback", memory_id=mem.id, before=before, after=mem, extra={"verdict": verdict})
        if tracer is not None:
            tracer.event("hmem", "feedback", {"memory_id": memory_id, "verdict": verdict})
        return mem

    def promote(self, memory_id: str, *, reason: str = "explicit") -> Memory | None:
        store, ledger, _tracer = self._wire()
        before = store.get(memory_id)
        mem = store.promote(memory_id, reason=reason)
        if mem is not None:
            ledger.record("promote", memory_id=memory_id, before=before, after=mem, extra={"reason": reason})
        return mem

    def decay(self) -> int:
        store, ledger, tracer = self._wire()
        n = store.decay()
        if tracer is not None:
            tracer.event("hmem", "decay", {"updated": n})
        _ = ledger
        return n


hmem = _HMemFacade()

__all__ = [
    "MemoryMiningService",
    "MemoryRetrievalService",
    "MemoryStoreService",
    "TrustLedgerService",
    "feedback",
    "hmem",
    "hmem_db_path",
    "mine",
    "retrieve",
]


def mine(event: MineEvent | dict[str, Any]) -> list[Memory]:
    return hmem.mine(event)


def retrieve(
    query: str,
    entity_ids: list[str] | None = None,
    top_n: int = 5,
    threshold: float = 0.35,
) -> dict[str, Any]:
    return hmem.retrieve(query, entity_ids=entity_ids, top_n=top_n, threshold=threshold)


def feedback(memory_id: str, verdict: Literal["helpful", "wrong"]) -> Memory | None:
    return hmem.feedback(memory_id, verdict)
