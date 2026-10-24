"""On-demand food/place suggestions from preference profiles."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from recall.engines.hmem.models import MineEvent
from recall.engines.world.preferences import get_preferences
from recall.schemas import (
    Brief,
    Evidence,
    PlaceSuggestion,
    RunState,
    SuggestResult,
    TalkingPoint,
)
from recall.tracing import Tracer

_DISLIKE_CUISINE = re.compile(
    r"(?i)\b(sushi|ramen|pizza|taco|burger|seafood|steak)\b"
)


async def run(state: RunState, deps: Any) -> RunState:
    """Resolve preferences and fill a brief-like suggest result on state."""
    entity_id = state.entity_id or ""
    person = state.entity_name or (state.intent.person_name if state.intent else "them")
    if not entity_id:
        state.final_brief = Brief(
            person=person,
            headline="I need to know who you mean before suggesting a place.",
            points=[],
            caveats=["Could not resolve the person."],
        )
        state.status = "done"
        return state

    settings = getattr(deps, "settings", None)
    if settings is not None and not getattr(settings, "preference_profiles", True):
        state.final_brief = Brief(
            person=person,
            headline="Preference profiles are turned off.",
            points=[],
            caveats=["PREFERENCE_PROFILES=false"],
        )
        state.status = "done"
        return state

    profile = get_preferences(
        entity_id,
        store=deps.world_store,
        settings=deps.settings,
        query_layer=deps.query_layer,
        tracer=deps.tracer,
        reason="suggest",
    )
    if profile.opted_out:
        state.final_brief = Brief(
            person=person,
            headline=f"Preferences for {person} are opted out.",
            points=[],
            caveats=["Ask them directly."],
        )
        state.status = "done"
        return state

    # Map profile items into ephemeral evidence for citation.
    evidence: dict[str, Evidence] = {}
    now = datetime.now(timezone.utc)
    for index, item in enumerate(profile.items, start=1):
        eid = f"p{index}"
        evidence[eid] = Evidence(
            id=eid,
            source="memory",
            source_ref=(item.source_refs[0] if item.source_refs else eid),
            ts=now,
            text=f"{item.sentiment} {item.kind}: {item.value}",
            tags=["preference", item.kind, item.sentiment],
        )
    state.evidence = evidence

    user_likes = await _user_food_memories(deps)
    suggestions = _rank_suggestions(profile.items, evidence, user_likes=user_likes)
    if not suggestions:
        result = SuggestResult(
            person=person,
            suggestions=[],
            spoken_summary=(
                f"I don't know what {person} likes to eat yet — ask them."
            ),
            caveats=["No preference evidence found."],
        )
    else:
        lines = [
            f"{s.place_or_cuisine}: {s.reason}" for s in suggestions[:3]
        ]
        result = SuggestResult(
            person=person,
            suggestions=suggestions[:3],
            spoken_summary=" ".join(lines),
            caveats=[],
        )

    # TODO(step6): Places API for opening hours and distance.
    points = [
        TalkingPoint(
            text=f"{s.place_or_cuisine} — {s.reason}",
            evidence_ids=list(s.evidence_ids),
            kind="logistics",
            priority=i + 1,
        )
        for i, s in enumerate(result.suggestions)
    ]
    state.final_brief = Brief(
        person=person,
        headline=result.spoken_summary or f"No food ideas for {person} yet.",
        points=points,
        caveats=list(result.caveats)
        + (["Places hours/distance not checked yet."] if points else []),
    )
    state.context["suggest_result"] = result.model_dump()
    state.status = "done"
    _log(
        deps,
        "suggester",
        f"suggestions={len(result.suggestions)} person={person!r}",
        {"suggest": result.model_dump()},
    )
    return state


def _rank_suggestions(
    items: list[Any],
    evidence: dict[str, Evidence],
    *,
    user_likes: list[str],
) -> list[PlaceSuggestion]:
    dislikes = {
        i.value.lower()
        for i in items
        if i.sentiment == "dislikes" or i.kind == "dislike"
    }
    diets = [
        i.value.lower()
        for i in items
        if i.kind == "dietary_stated"
    ]

    scored: list[tuple[float, PlaceSuggestion]] = []
    id_by_value = {
        evidence[eid].text.split(": ", 1)[-1].lower(): eid for eid in evidence
    }

    for item in items:
        if item.sentiment == "dislikes" or item.kind == "dislike":
            continue
        if item.kind == "dietary_stated":
            continue
        value_l = item.value.lower()
        if any(d in value_l or value_l in d for d in dislikes):
            continue
        if diets and _conflicts_diet(value_l, diets):
            continue
        eid = id_by_value.get(item.value.lower())
        evidence_ids = [eid] if eid else []
        # Rebuild evidence id from tags match
        if not evidence_ids:
            for eid, ev in evidence.items():
                if item.value.lower() in ev.text.lower():
                    evidence_ids = [eid]
                    break
        # Prefer named restaurants they praised over cuisine tokens / calendar spots.
        score = float(item.mention_count) * 2.5 + float(item.confidence)
        if item.kind == "restaurant":
            score += 5.0
        elif item.kind in {"cafe", "bar"}:
            score += 3.0
        elif item.kind == "hangout":
            score += 1.0
        elif item.kind == "cuisine":
            score += 0.5
        if item.mention_count >= 2 and item.kind in {"restaurant", "cafe", "bar"}:
            score += 4.0
        if any(u.lower() in value_l for u in user_likes):
            score += 0.5
        is_place = item.kind in {"restaurant", "cafe", "bar", "hangout"}
        when = _relative(item.last_mentioned)
        reason = (
            f"they mentioned it positively{when}"
            if item.mention_count
            else f"from shared plans{when}"
        )
        if item.mention_count >= 2:
            reason = f"they praised it more than once{when}"
        scored.append(
            (
                score,
                PlaceSuggestion(
                    place_or_cuisine=item.value,
                    reason=reason,
                    evidence_ids=evidence_ids,
                    is_specific_place=is_place,
                ),
            )
        )

    scored.sort(key=lambda pair: pair[0], reverse=True)
    # Dedupe by lower value
    out: list[PlaceSuggestion] = []
    seen: set[str] = set()
    for _, sug in scored:
        key = sug.place_or_cuisine.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(sug)
    return out


def _conflicts_diet(value: str, diets: list[str]) -> bool:
    if "vegetarian" in diets or "vegan" in diets:
        if re.search(r"(?i)\b(steak|burger|bacon|pepperoni|sushi)\b", value):
            return True
    return False


def _relative(ts: str) -> str:
    if not ts:
        return ""
    try:
        when = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return ""
    days = (datetime.now(timezone.utc) - when).days
    if days <= 1:
        return " recently"
    if days < 14:
        return f" about {days} days ago"
    if days < 60:
        return f" about {days // 7} weeks ago"
    return f" about {days // 30} months ago"


async def _user_food_memories(deps: Any) -> list[str]:
    """Pull the user's own food likes from H-MEM if present."""
    try:
        result = await deps.hmem_retrieval.retrieve(
            "food restaurant lunch dinner cafe",
            entity_ids=[],
            top_n=5,
            threshold=0.2,
        )
    except Exception:  # noqa: BLE001
        return []
    likes: list[str] = []
    for mem in result.get("memories") or []:
        text = str(mem.get("content") or "")
        if re.search(r"(?i)\b(like|love|favourite|favorite)\b", text):
            likes.append(text)
    _ = MineEvent  # imported for type clarity / future mine hooks
    return likes


def _log(deps: Any, agent: str, summary: str, payload: dict[str, Any]) -> None:
    tracer = getattr(deps, "tracer", None) or Tracer.current()
    if tracer is not None:
        tracer.event(agent, "complete", {"summary": summary, **payload})
