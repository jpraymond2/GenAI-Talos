"""Build world context for resolved entities."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from rapidfuzz import fuzz

from recall.config import Settings, get_settings
from recall.data.queries import (
    InteractionRow,
    all_events_in_window,
    all_interactions,
    resolve_now,
)
from recall.data.query_layer import QueryLayer, get_query_layer
from recall.engines.world.models import PersonContext, ResolveCandidate
from recall.engines.world.store import WorldStore, world_db_path
from recall.tracing import Tracer

_OPEN_LOOP_RE = re.compile(
    r"(?i)\b(can you|could you|would you|please\b|intro\b)|[?？]"
)


def _parse_ts(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return datetime.now(timezone.utc)


class ContextBuilderService:
    """Resolve names and assemble structured person context."""

    def __init__(
        self,
        store: WorldStore | None = None,
        settings: Settings | None = None,
        query_layer: QueryLayer | None = None,
        tracer: Tracer | None = None,
    ) -> None:
        self._settings = settings if settings is not None else get_settings()
        self._store = store if store is not None else WorldStore(settings=self._settings)
        self._layer = query_layer
        self._tracer = tracer if tracer is not None else Tracer.current()

    def _ql(self) -> QueryLayer:
        if self._layer is not None:
            return self._layer
        return get_query_layer(self._settings, tracer=self._tracer)

    def resolve(self, name: str) -> list[ResolveCandidate]:
        """Return ranked person candidates for ``name`` (caller must disambiguate close scores)."""
        needle = (name or "").strip()
        if not needle:
            return []
        people = self._store.list_entities(type="person")
        candidates: list[ResolveCandidate] = []
        needle_l = needle.lower()
        for entity in people:
            alias_pool = [entity.canonical_name, *entity.aliases]
            best_score = 0.0
            best_reason = "no match"
            for alias in alias_pool:
                alias_l = alias.lower()
                if alias_l == needle_l:
                    score = 1.0
                    reason = "exact alias match"
                elif needle_l in alias_l or alias_l in needle_l:
                    # Substring / first-name hits: keep scores close so callers must ask.
                    ratio = fuzz.token_set_ratio(needle, alias) / 100.0
                    score = max(0.82, min(0.92, ratio))
                    reason = "partial name match"
                else:
                    ratio = fuzz.token_set_ratio(needle, alias) / 100.0
                    score = ratio
                    reason = f"fuzzy name ({int(ratio * 100)})"
                if score > best_score:
                    best_score = score
                    best_reason = reason
            if best_score < 0.55:
                continue
            candidates.append(
                ResolveCandidate(
                    entity_id=entity.id,
                    name=entity.canonical_name,
                    score=round(best_score, 4),
                    reason=best_reason,
                )
            )
        # Sort by score desc, then name — equal scores stay close; do not prefer Haddad.
        candidates.sort(key=lambda c: (-c.score, c.name))
        if self._tracer is not None:
            self._tracer.event(
                "world",
                "resolve",
                {"query": needle, "candidate_count": len(candidates)},
            )
        return candidates

    def person_context(
        self,
        entity_id: str,
        budget_tokens: int = 1500,
    ) -> dict[str, Any]:
        """Return a structured person brief (dict), respecting a soft token budget."""
        _ = budget_tokens  # soft budget: we already cap lists below
        entity = self._store.get_entity(entity_id)
        if entity is None:
            return {}

        layer = self._ql()
        handle_values = [
            a
            for a in entity.aliases
            if "@" in a or a.startswith("+") or "whatsapp.net" in a
        ]
        # Include display-name aliases only as interaction handle_values when they appear as such.
        interactions = all_interactions(
            source_tag="world.context.interactions",
            since_days=400,
            limit=5000,
            settings=self._settings,
            layer=layer,
        )
        phone_norms = {re.sub(r"\D", "", a) for a in handle_values if a.startswith("+") or a.isdigit()}

        def matches_entity(row: InteractionRow) -> bool:
            hv = row.handle_value
            if hv in handle_values or hv in entity.aliases:
                return True
            digits = re.sub(r"\D", "", hv.split("@", 1)[0])
            return bool(digits and digits in phone_norms)

        related = [row for row in interactions if matches_entity(row)]
        related.sort(key=lambda r: r.ts_utc, reverse=True)

        last_per_source: dict[str, dict[str, Any]] = {}
        for row in related:
            if row.source not in last_per_source:
                last_per_source[row.source] = {
                    "ts_utc": row.ts_utc,
                    "direction": row.direction,
                    "source_ref": row.source_ref,
                    "subject": row.subject,
                }

        recent = [
            {
                "interaction_id": row.interaction_id,
                "source": row.source,
                "thread_id": row.thread_id,
                "direction": row.direction,
                "ts_utc": row.ts_utc,
                "subject": row.subject,
                "text": row.text[:240],
                "source_ref": row.source_ref,
            }
            for row in related[:15]
        ]

        open_loops = self._find_open_loops(related)

        emails = [a for a in entity.aliases if "@" in a and "whatsapp.net" not in a]
        events = all_events_in_window(
            source_tag="world.context.events",
            days_back=400,
            days_forward=60,
            settings=self._settings,
            layer=layer,
        )
        now = resolve_now(self._settings)
        now_s = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        email_set = {e.lower() for e in emails}
        person_events = [
            e
            for e in events
            if any(att.lower() in email_set for att in e.attendees)
        ]
        upcoming = [
            e.model_dump()
            for e in person_events
            if e.start_utc >= now_s
        ]
        past = [
            e.model_dump()
            for e in person_events
            if e.start_utc < now_s
        ]
        past.sort(key=lambda e: e["start_utc"], reverse=True)

        conflicts = self._store.list_conflicts(entity_id)
        ctx = PersonContext(
            entity=entity,
            aliases=list(entity.aliases),
            last_interaction_per_source=last_per_source,
            recent_interactions=recent,
            open_loops=open_loops,
            upcoming_events=upcoming[:10],
            past_events=past[:20],
            conflicts=conflicts,
            selection_reason=f"entity_id={entity_id}",
        )
        if self._tracer is not None:
            self._tracer.event(
                "world",
                "person_context",
                {
                    "entity_id": entity_id,
                    "recent": len(recent),
                    "open_loops": len(open_loops),
                    "past_events": len(past),
                    "conflicts": len(conflicts),
                },
            )
        return ctx.model_dump()

    @staticmethod
    def _find_open_loops(related: list[InteractionRow]) -> list[dict[str, Any]]:
        """Inbound request/question with no later outbound in the same thread."""
        by_thread: dict[str, list[InteractionRow]] = {}
        for row in related:
            by_thread.setdefault(row.thread_id, []).append(row)

        loops: list[dict[str, Any]] = []
        for thread_id, rows in by_thread.items():
            ordered = sorted(rows, key=lambda r: r.ts_utc)
            for idx, row in enumerate(ordered):
                if row.direction != "in":
                    continue
                blob = f"{row.subject} {row.text}"
                if not _OPEN_LOOP_RE.search(blob):
                    continue
                later_out = any(
                    later.direction == "out" and later.ts_utc >= row.ts_utc
                    for later in ordered[idx + 1 :]
                )
                if later_out:
                    continue
                loops.append(
                    {
                        "thread_id": thread_id,
                        "ts_utc": row.ts_utc,
                        "subject": row.subject,
                        "text": row.text[:300],
                        "source": row.source,
                        "source_ref": row.source_ref,
                    }
                )
        loops.sort(key=lambda item: item["ts_utc"], reverse=True)
        return loops


def build_context(entity_ids: list[str]) -> list[dict[str, Any]]:
    """Facade-friendly helper: person_context for each id."""
    service = ContextBuilderService()
    return [service.person_context(entity_id) for entity_id in entity_ids]
