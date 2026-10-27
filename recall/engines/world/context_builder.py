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
    interactions_for_entity_handles,
    resolve_now,
)
from recall.data.query_layer import QueryLayer, get_query_layer
from recall.engines.world.models import PersonContext, ResolveCandidate
from recall.engines.world.store import WorldStore, world_db_path
from recall.tracing import Tracer

_OPEN_LOOP_RE = re.compile(
    r"(?i)\b(can you|could you|would you|please\b|intro\b)|[?？]"
)
_PROMISE_RE = re.compile(
    r"(?i)\b("
    r"i(?:'ll| will) send|will send(?: it| them)?|"
    r"i promise|"
    r"sending (?:it |them )?(?:by|before|tomorrow|tonight|"
    r"monday|tuesday|wednesday|thursday|friday)"
    r")\b"
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
        owner_name = (self._settings.dataset_owner or "").strip().lower()
        owner_id = self._store.get_meta("user_entity_id")
        candidates: list[ResolveCandidate] = []
        needle_l = needle.lower()
        for entity in people:
            # Never offer the dataset owner as a meeting candidate.
            if owner_id and entity.id == owner_id:
                continue
            if owner_name and entity.canonical_name.strip().lower() == owner_name:
                continue
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
        # Sort by score desc, then name — equal scores stay alphabetical, no fixed preferred.
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
        handle_links = self._store.list_handle_links(entity_id=entity_id)
        linked_values = [
            str(row.get("handle_value") or "")
            for row in handle_links
            if row.get("handle_value")
        ]
        # Push entity handles + date window + LIMIT into SQL (no full-table fetch).
        related = interactions_for_entity_handles(
            list(entity.aliases),
            source_tag="world.context.interactions",
            linked_handle_values=linked_values,
            since_days=400,
            limit=200,
            settings=self._settings,
            layer=layer,
        )
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
        upcoming.sort(key=lambda e: e["start_utc"])
        past = [
            e.model_dump()
            for e in person_events
            if e.start_utc < now_s
        ]
        past.sort(key=lambda e: e["start_utc"], reverse=True)

        # Last contact = newest message (not calendar) at or before frozen now.
        message_sources = {"gmail", "imessage", "whatsapp", "messenger"}
        last_contact: dict[str, Any] | None = None
        for row in related:
            if row.source not in message_sources:
                continue
            if row.ts_utc > now_s:
                continue
            last_contact = {
                "ts_utc": row.ts_utc,
                "source": row.source,
                "direction": row.direction,
                "source_ref": row.source_ref,
                "subject": row.subject,
            }
            break  # related is newest-first

        next_meeting: dict[str, Any] | None = None
        for ev in upcoming:
            title = str(ev.get("title") or "")
            if "cancel" in title.lower():
                continue
            next_meeting = {
                "title": title,
                "start_utc": ev.get("start_utc"),
                "location": ev.get("location") or "",
                "source_ref": ev.get("source_ref") or "",
            }
            break

        conflicts = self._store.list_conflicts(entity_id)
        linked_handles = [
            {
                "handle_kind": row.get("handle_kind"),
                "handle_value": row.get("handle_value"),
                "probability": float(row.get("confidence") or 0),
                "status": row.get("status"),
                "source_ref": row.get("source_ref"),
            }
            for row in handle_links
        ]
        tentative_values = {
            str(h["handle_value"]).lower()
            for h in linked_handles
            if h.get("status") in {"assumed", "tentative"}
        }
        for item in recent:
            # Mark evidence from tentative handles for writer/critic.
            # source_ref alone may not include handle; use interaction join via text path.
            item["assumption"] = "assumed" if item.get("source") == "gmail" and tentative_values else "anchored"

        # Refine assumption using handle_value when present on interaction rows
        related_by_id = {row.interaction_id: row for row in related}
        for item in recent:
            src_row = related_by_id.get(item["interaction_id"])
            if src_row is None:
                continue
            hv = (src_row.handle_value or "").lower()
            item["assumption"] = "assumed" if hv in tentative_values else "anchored"

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
        payload = ctx.model_dump()
        payload["linked_handles"] = linked_handles
        payload["last_contact"] = last_contact
        payload["next_meeting"] = next_meeting
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
                    "linked_handles": len(linked_handles),
                },
            )
        return payload

    def _find_open_loops(self, related: list[InteractionRow]) -> list[dict[str, Any]]:
        """Unanswered inbound asks + owner promises (ts <= frozen now)."""
        now_s = resolve_now(self._settings).strftime("%Y-%m-%dT%H:%M:%SZ")
        by_thread: dict[str, list[InteractionRow]] = {}
        for row in related:
            if row.ts_utc > now_s:
                continue
            by_thread.setdefault(row.thread_id, []).append(row)

        loops: list[dict[str, Any]] = []
        for thread_id, rows in by_thread.items():
            ordered = sorted(rows, key=lambda r: r.ts_utc)
            for idx, row in enumerate(ordered):
                blob = f"{row.subject} {row.text}"
                if row.direction == "in":
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
                            "loop_kind": "ask",
                        }
                    )
                elif row.direction == "out" and _PROMISE_RE.search(blob):
                    # Still open unless a later outbound looks like delivery.
                    later_delivery = any(
                        later.direction == "out"
                        and later.ts_utc > row.ts_utc
                        and re.search(
                            r"(?i)\b(sent|attached|here (?:it|they) are|done|shipped)\b",
                            f"{later.subject} {later.text}",
                        )
                        for later in ordered[idx + 1 :]
                    )
                    if later_delivery:
                        continue
                    loops.append(
                        {
                            "thread_id": thread_id,
                            "ts_utc": row.ts_utc,
                            "subject": row.subject,
                            "text": row.text[:300],
                            "source": row.source,
                            "source_ref": row.source_ref,
                            "loop_kind": "promise",
                            "prior_ask": next(
                                (
                                    earlier.text[:200]
                                    for earlier in reversed(ordered[:idx])
                                    if earlier.direction == "in"
                                ),
                                "",
                            ),
                        }
                    )
        loops.sort(key=lambda item: item["ts_utc"], reverse=True)
        return loops


def build_context(entity_ids: list[str]) -> list[dict[str, Any]]:
    """Facade-friendly helper: person_context for each id."""
    service = ContextBuilderService()
    return [service.person_context(entity_id) for entity_id in entity_ids]
