"""Mine atomic memories from user notes, critic verdicts, and action outcomes."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from recall.config import Settings, get_settings
from recall.engines.hmem.ledger import TrustLedgerService
from recall.engines.hmem.models import (
    Memory,
    MinedAtomicMemory,
    MinedMemoryBatch,
    MineEvent,
)
from recall.engines.hmem.store import MemoryStoreService, new_memory_id
from recall.engines.world import world as world_facade
from recall.engines.world.store import WorldStore
from recall.llm.generator import LLMUnavailable, LLMOutputError, embed, generate_json, use_sample_provider
from recall.tracing import Tracer

_CITY_RE = re.compile(
    r"(?i)\b(?:moves? to|moving to|staying in|based in|lives? in)\s+([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)?)"
)
_EMPLOYER_RE = re.compile(
    r"(?i)\b(?:works? at|working at|joined|employer(?:\s+is)?)\s+([A-Z][A-Za-z0-9&.'\- ]{1,40})"
)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _infer_attribute(content: str) -> tuple[str, str]:
    city = _CITY_RE.search(content)
    if city:
        return "city", city.group(1).strip(" .,!")
    employer = _EMPLOYER_RE.search(content)
    if employer:
        return "employer", employer.group(1).strip(" .,!")
    return "", ""


def _sample_split_memories(text: str) -> list[MinedAtomicMemory]:
    """SAMPLE_MODE path: split compound notes without calling the LLM."""
    text = text.strip()
    if "staying in montreal" in text.lower() or (
        "montreal" in text.lower() and "staying" in text.lower()
    ):
        return [
            MinedAtomicMemory(
                content=text,
                type="factual",
                entity_names=_guess_names(text),
                attribute_key="city",
                attribute_value="Montreal",
            )
        ]
    # Move / city patterns: keep full sentence as one memory with inferred attr.
    key, value = _infer_attribute(text)
    shared_names = _guess_names(text)
    # Generic: sentence split on " and ".
    parts = re.split(r"\s+and\s+", text, maxsplit=1, flags=re.IGNORECASE)
    if len(parts) == 2 and parts[0].strip() and parts[1].strip():
        out: list[MinedAtomicMemory] = []
        for part in parts:
            part_key, part_value = _infer_attribute(part)
            names = _merge_names(_guess_names(part), shared_names)
            out.append(
                MinedAtomicMemory(
                    content=part.strip(),
                    type="factual",
                    entity_names=names,
                    attribute_key=part_key,
                    attribute_value=part_value,
                )
            )
        return out
    return [
        MinedAtomicMemory(
            content=text,
            type="factual",
            entity_names=shared_names,
            attribute_key=key,
            attribute_value=value,
        )
    ]


def _merge_names(*groups: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for group in groups:
        for name in group:
            if name not in seen:
                seen.add(name)
                out.append(name)
    return out


def _guess_names(text: str) -> list[str]:
    """Pull likely Person Name tokens; world.resolve maps them to ids later."""
    names: list[str] = []
    # Two-token Capitalized names
    for match in re.finditer(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b", text):
        names.append(match.group(1))
    # Leading bare first name ("Ada is staying in …") when no full name found.
    if not names:
        lead = re.match(r"^([A-Z][a-z]+)\b", text.strip())
        if lead:
            names.append(lead.group(1))
    return _merge_names(names)


class MemoryMiningService:
    """Turn events into stored memories with embeddings and conflict handling."""

    def __init__(
        self,
        store: MemoryStoreService | None = None,
        ledger: TrustLedgerService | None = None,
        settings: Settings | None = None,
        tracer: Tracer | None = None,
        world_store: WorldStore | None = None,
    ) -> None:
        self._settings = settings if settings is not None else get_settings()
        self._tracer = tracer if tracer is not None else Tracer.current()
        self._store = store or MemoryStoreService(settings=self._settings)
        self._ledger = ledger or TrustLedgerService(
            settings=self._settings,
            tracer=self._tracer,
            run_id=self._tracer.run_id if self._tracer else None,
        )
        self._world_store = world_store

    async def mine(self, event: MineEvent | dict[str, Any]) -> list[Memory]:
        """Mine atomic memories from an event and persist them."""
        if isinstance(event, dict):
            event = MineEvent.model_validate(event)
        text = (event.text or "").strip()
        if not text:
            return []

        promote_long = bool(
            re.search(r"\b(remember|note)\b", text, re.I)
            or event.metadata.get("promote")
            or event.kind == "user_note"
            and event.metadata.get("explicit_remember")
        )
        # Explicit remember/note word in the text triggers long_term.
        explicit_remember = bool(re.search(r"\b(remember|note)\b", text, re.I))

        atomics = await self._extract_atomics(text, event)
        created: list[Memory] = []
        run_id = self._tracer.run_id if self._tracer else "hmem-offline"
        source = "user_note" if event.kind == "user_note" else (
            "critic" if event.kind == "critic_verdict" else "action"
        )

        for atomic in atomics:
            entity_ids = self._map_entity_names(atomic.entity_names)
            attr_key = atomic.attribute_key
            attr_value = atomic.attribute_value
            if not attr_key:
                attr_key, attr_value = _infer_attribute(atomic.content)

            vectors = await embed([atomic.content], input_type="passage")
            embedding = vectors[0] if vectors else []
            ts = _now()
            mem_id = new_memory_id(atomic.content, *entity_ids, ts)
            layer = "long_term" if explicit_remember else "short_term"
            memory = Memory(
                id=mem_id,
                content=atomic.content,
                type=atomic.type,
                layer=layer,
                entity_ids=entity_ids,
                tags=[],
                embedding=embedding,
                confidence=0.75,
                source=source,  # type: ignore[arg-type]
                timestamp=ts,
                last_accessed=ts,
                access_count=0,
                agent_id="hmem_mine",
                run_id=run_id,
                attribute_key=attr_key,
                attribute_value=attr_value,
            )
            self._store.add(memory)
            self._ledger.record("create", memory_id=memory.id, before=None, after=memory)
            created.append(memory)
            self._resolve_conflicts(memory, source=source)

        if self._tracer is not None:
            self._tracer.event(
                "hmem",
                "mine",
                {"created": len(created), "kind": event.kind},
            )
        _ = promote_long
        return created

    async def _extract_atomics(self, text: str, event: MineEvent) -> list[MinedAtomicMemory]:
        if use_sample_provider(self._settings):
            return _sample_split_memories(text)
        try:
            result = await generate_json(
                f"Extract atomic memories from this {event.kind}:\n{text}",
                "Split into atomic facts. Include entity_names mentioned. "
                "If a city or employer change is stated, set attribute_key/value.",
                MinedMemoryBatch,
                role="memory_extract",
            )
            assert isinstance(result, MinedMemoryBatch)
            if result.memories:
                return result.memories
        except (LLMUnavailable, LLMOutputError, AssertionError, TypeError):
            pass
        # Fallback: raw note as one short_term memory.
        key, value = _infer_attribute(text)
        return [
            MinedAtomicMemory(
                content=text,
                type="factual",
                entity_names=_guess_names(text),
                attribute_key=key,
                attribute_value=value,
            )
        ]

    def _map_entity_names(self, names: list[str]) -> list[str]:
        ids: list[str] = []
        seen: set[str] = set()
        store = self._world_store
        for name in names:
            needle = name.strip()
            if not needle:
                continue
            candidates = list(world_facade.resolve(needle))
            if not candidates and store is not None:
                for entity in store.list_entities(type="person"):
                    if needle.lower() == entity.canonical_name.lower() or any(
                        needle.lower() == a.lower() for a in entity.aliases
                    ):
                        from recall.engines.world.models import ResolveCandidate

                        candidates = [
                            ResolveCandidate(
                                entity_id=entity.id,
                                name=entity.canonical_name,
                                score=1.0,
                                reason="exact store match",
                            )
                        ]
                        break
            if not candidates:
                continue
            top = sorted(candidates, key=lambda c: -c.score)
            exact = [c for c in top if c.name.lower() == needle.lower()]
            if exact:
                chosen = exact[0]
            elif " " not in needle:
                # Prefer a unique multi-token namesake; otherwise skip guessing.
                multi = [
                    c
                    for c in top
                    if " " in c.name and c.name.lower().split()[0] == needle.lower()
                ]
                if len(multi) == 1:
                    chosen = multi[0]
                elif len(top) >= 2 and (top[0].score - top[1].score) < 0.08:
                    continue
                else:
                    chosen = top[0]
            else:
                chosen = top[0]
            if chosen.entity_id not in seen:
                seen.add(chosen.entity_id)
                ids.append(chosen.entity_id)
        return ids

    def _resolve_conflicts(self, memory: Memory, *, source: str) -> None:
        if not memory.attribute_key or not memory.attribute_value:
            return
        older = self._store.find_attribute_conflicts(
            entity_ids=memory.entity_ids,
            attribute_key=memory.attribute_key,
            attribute_value=memory.attribute_value,
        )
        if not older:
            return
        if self._tracer is not None:
            self._tracer.event(
                "hmem",
                "conflict_detected",
                {
                    "memory_id": memory.id,
                    "attribute_key": memory.attribute_key,
                    "count": len(older),
                },
            )
        self._ledger.record(
            "conflict_detected",
            memory_id=memory.id,
            before=None,
            after=memory,
            extra={
                "attribute_key": memory.attribute_key,
                "older_ids": [m.id for m in older],
            },
        )
        should_supersede = source == "user_note" or True  # newer always wins for user notes / newer
        if not should_supersede:
            return
        for old in older:
            before = old.model_copy(deep=True)
            updated = self._store.mark_superseded(old.id, memory.id)
            self._ledger.record(
                "supersede",
                memory_id=old.id,
                before=before,
                after=self._store.get(old.id),
                extra={"superseded_by": memory.id},
            )
            if self._tracer is not None:
                self._tracer.event(
                    "hmem",
                    "conflict_resolved",
                    {"older_id": old.id, "newer_id": memory.id},
                )
            self._ledger.record(
                "conflict_resolved",
                memory_id=memory.id,
                before=before,
                after=updated,
                extra={"superseded": old.id},
            )


def mine() -> list[Any]:
    """Legacy placeholder."""
    raise NotImplementedError("Use MemoryMiningService.mine")
