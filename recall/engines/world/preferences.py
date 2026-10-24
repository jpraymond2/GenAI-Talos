"""On-demand food/places preference profiles (never bulk-built at ingest)."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from recall.config import Settings, get_settings
from recall.data.queries import (
    InteractionRow,
    all_events_in_window,
    all_interactions,
    resolve_now,
)
from recall.data.query_layer import QueryLayer, get_query_layer
from recall.engines.world.models import PreferenceItem, PreferenceProfile
from recall.engines.world.store import WorldStore, world_db_path
from recall.llm.generator import (
    LLMUnavailable,
    LLMOutputError,
    generate_json,
    use_sample_provider,
)
from recall.tracing import Tracer

logger = logging.getLogger(__name__)

SCORING_VERSION = 1
CACHE_DAYS = 14
REBUILD_NEW_INTERACTIONS = 20
MAX_SNIPPETS = 40

_FOOD_RE = re.compile(
    r"(?i)\b("
    r"food|eat|eating|lunch|dinner|brunch|breakfast|coffee|café|cafe|"
    r"restaurant|bar|drinks|place|spot|cuisine|menu|hungry|bite|"
    r"pizza|sushi|ramen|taco|burger|bakery|baklava|manakish|mezze|"
    r"lebanese|italian|japanese|mexican|thai|indian|korean|vegan|"
    r"vegetarian|halal|kosher|gluten|allergy|allergic|"
    r"let'?s\s+meet\s+at|meet\s+at|grab\s+(?:a\s+)?(?:coffee|lunch|dinner|drink)"
    r")\b"
)
_SENSITIVE_INFER_RE = re.compile(
    r"(?i)\b(religion|muslim|jewish|christian|hindu|buddhist|"
    r"allerg(?:y|ic|ies)|diabetes|illness|disease)\b"
)


class _ExtractedItem(BaseModel):
    kind: str = "cuisine"
    value: str = ""
    sentiment: str = "likes"
    source_refs: list[str] = Field(default_factory=list)
    confidence: float = 0.6
    last_mentioned: str = ""


class _ExtractBatch(BaseModel):
    items: list[_ExtractedItem] = Field(default_factory=list)


def get_preferences(
    entity_id: str,
    topic: str = "food_places",
    *,
    store: WorldStore | None = None,
    settings: Settings | None = None,
    query_layer: QueryLayer | None = None,
    tracer: Tracer | None = None,
    reason: str = "on_demand",
    force: bool = False,
) -> PreferenceProfile:
    """Build or return cached food/places preferences for one person."""
    active = settings if settings is not None else get_settings()
    if not getattr(active, "preference_profiles", True):
        return PreferenceProfile(
            entity_id=entity_id,
            topic=topic,
            opted_out=True,
            built_at=_now_s(active),
        )
    world = store if store is not None else WorldStore(path=world_db_path(active), settings=active)
    layer = query_layer if query_layer is not None else get_query_layer(active, tracer=tracer)
    tr = tracer if tracer is not None else Tracer.current()

    cached = world.get_preference_profile(entity_id, topic)
    if cached is not None and cached.opted_out:
        return cached

    entity = world.get_entity(entity_id)
    rows = _interactions_for_entity(entity, layer=layer, settings=active) if entity else []
    now = resolve_now(active)
    needs_rebuild = force or cached is None
    if cached is not None and not force:
        try:
            built = datetime.fromisoformat(cached.built_at.replace("Z", "+00:00"))
        except ValueError:
            built = now - timedelta(days=CACHE_DAYS + 1)
        age_ok = (now - built) < timedelta(days=CACHE_DAYS)
        growth = len(rows) - int(cached.interaction_count_at_build)
        if age_ok and growth < REBUILD_NEW_INTERACTIONS:
            needs_rebuild = False

    if not needs_rebuild and cached is not None:
        return cached

    profile = _build_profile(
        entity_id,
        topic=topic,
        entity=entity,
        rows=rows,
        layer=layer,
        settings=active,
    )
    world.upsert_preference_profile(profile)
    _log_ledger(
        active,
        tr.run_id if tr is not None else "prefs-offline",
        entity_id=entity_id,
        item_count=len(profile.items),
        reason=reason,
    )
    if tr is not None:
        tr.event(
            "world",
            "preferences_build",
            {
                "entity_id": entity_id,
                "items": len(profile.items),
                "reason": reason,
            },
        )
    return profile


def opt_out_preferences(
    entity_id: str,
    topic: str = "food_places",
    *,
    store: WorldStore | None = None,
    settings: Settings | None = None,
) -> PreferenceProfile:
    """Delete profile and block rebuilding until cleared."""
    active = settings if settings is not None else get_settings()
    world = store if store is not None else WorldStore(path=world_db_path(active), settings=active)
    tombstone = PreferenceProfile(
        entity_id=entity_id,
        topic=topic,
        items=[],
        built_at=_now_s(active),
        scoring_version=SCORING_VERSION,
        opted_out=True,
    )
    world.upsert_preference_profile(tombstone)
    return tombstone


def clear_preference_opt_out(
    entity_id: str,
    topic: str = "food_places",
    *,
    store: WorldStore | None = None,
    settings: Settings | None = None,
) -> None:
    """Allow rebuilding after an opt-out."""
    active = settings if settings is not None else get_settings()
    world = store if store is not None else WorldStore(path=world_db_path(active), settings=active)
    world.delete_preference_profile(entity_id, topic)


def _build_profile(
    entity_id: str,
    *,
    topic: str,
    entity: Any,
    rows: list[InteractionRow],
    layer: QueryLayer,
    settings: Settings,
) -> PreferenceProfile:
    now = resolve_now(settings)
    since = now - timedelta(days=365)
    recent = []
    for r in rows:
        try:
            ts = datetime.fromisoformat(r.ts_utc.replace("Z", "+00:00"))
        except ValueError:
            continue
        if ts >= since:
            recent.append(r)

    # Only inbound (what they said) + shared event locations.
    inbound = [r for r in recent if r.direction == "in"]
    snippets: list[dict[str, str]] = []
    for r in sorted(inbound, key=lambda x: x.ts_utc, reverse=True):
        blob = f"{r.subject or ''} {r.text or ''}"
        if not _FOOD_RE.search(blob):
            continue
        snippets.append(
            {
                "text": blob.strip()[:240],
                "source_ref": r.source_ref,
                "ts": r.ts_utc,
                "direction": r.direction,
                "handle": r.handle_value,
            }
        )
        if len(snippets) >= MAX_SNIPPETS:
            break

    emails = {
        a.lower()
        for a in (entity.aliases if entity else [])
        if "@" in a and "whatsapp" not in a
    }
    events = all_events_in_window(
        source_tag="world.preferences.events",
        days_back=400,
        days_forward=30,
        settings=settings,
        layer=layer,
    )
    for e in events:
        if not any(att.lower() in emails for att in e.attendees):
            continue
        loc = (e.location or "").strip()
        title = (e.title or "").strip()
        if not loc and not _FOOD_RE.search(title):
            continue
        snippets.append(
            {
                "text": f"calendar: {title} @ {loc}".strip(" @"),
                "source_ref": e.source_ref or e.event_id,
                "ts": e.start_utc,
                "direction": "event",
                "handle": "",
            }
        )

    items = _extract_items(snippets, settings=settings)
    # Drop sensitive inferences; keep only explicit dietary_stated from first person.
    cleaned: list[PreferenceItem] = []
    for item in items:
        if item.kind == "dietary_stated":
            if not _explicit_self_diet(item, snippets):
                continue
        if item.kind != "dietary_stated" and _SENSITIVE_INFER_RE.search(item.value):
            continue
        cleaned.append(item)

    return PreferenceProfile(
        entity_id=entity_id,
        topic=topic,
        items=cleaned,
        built_at=_now_s(settings),
        scoring_version=SCORING_VERSION,
        interaction_count_at_build=len(rows),
        opted_out=False,
    )


def _extract_items(
    snippets: list[dict[str, str]], *, settings: Settings
) -> list[PreferenceItem]:
    if not snippets:
        return []
    if use_sample_provider(settings):
        return _heuristic_extract(snippets)
    prompt = json.dumps(
        {
            "instruction": (
                "Extract only what THIS person said they like or dislike, or places "
                "you went together. Each item must cite source_refs from the snippets. "
                "Never infer religion, health, or allergies. dietary_stated only if "
                "they explicitly said it about themselves."
            ),
            "snippets": snippets,
        },
        ensure_ascii=False,
    )
    system = (
        "Extract food and place preferences. Return items with kind in "
        "{cuisine,restaurant,cafe,bar,hangout,neighbourhood,dislike,dietary_stated}."
    )
    try:
        import asyncio

        async def _run() -> _ExtractBatch:
            result = await generate_json(
                prompt, system, _ExtractBatch, role="memory_extract"
            )
            assert isinstance(result, _ExtractBatch)
            return result

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None and loop.is_running():
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                batch = pool.submit(lambda: asyncio.run(_run())).result(timeout=20)
        else:
            batch = asyncio.run(_run())
        return [_to_item(i) for i in batch.items if i.value.strip()]
    except (LLMUnavailable, LLMOutputError, TimeoutError, Exception) as exc:  # noqa: BLE001
        logger.info("preferences LLM fallback: %s", exc)
        return _heuristic_extract(snippets)


def _heuristic_extract(snippets: list[dict[str, str]]) -> list[PreferenceItem]:
    """Deterministic SAMPLE_MODE extractor from keyword patterns."""
    items: list[PreferenceItem] = []
    seen: set[str] = set()

    praise = re.compile(
        r"(?i)\b(?:best|love|loved|amazing|favourite|favorite|great)\b.{0,40}?"
        r"([A-Z][A-Za-z0-9&'’\- ]{2,40}(?:restaurant|café|cafe|kitchen|tavern|spot)?)"
    )
    named_place = re.compile(
        r"(?i)\b(?:back to|go to|at|craving)\s+"
        r"([A-Z][A-Za-z0-9'’\-]+(?:\s*&\s*[A-Z][A-Za-z0-9'’\-]+|"
        r"(?:\s+[A-Z][A-Za-z0-9'’\-]+){0,3}))"
    )
    not_fan = re.compile(
        r"(?i)\b(?:not a fan of|don'?t like|hate)\s+([A-Za-z]{3,20})\b"
    )
    diet = re.compile(
        r"(?i)\bI(?:'m| am)\s+(vegetarian|vegan|pescatarian|halal|kosher)\b"
    )
    meet_at = re.compile(
        r"(?i)\b(?:meet(?:ing)? at|at)\s+([A-Z][A-Za-z0-9&'’\- ]{2,40})"
    )
    cuisine = re.compile(
        r"(?i)\b(lebanese|italian|japanese|mexican|thai|indian|korean|sushi|"
        r"pizza|ramen|manakish|mezze)\b"
    )

    for snip in snippets:
        text = snip.get("text") or ""
        ref = snip.get("source_ref") or ""
        ts = snip.get("ts") or ""
        # Group-chat attribution: only trust inbound / calendar for that person.
        if snip.get("direction") not in {"in", "event"}:
            continue

        for match in not_fan.finditer(text):
            value = match.group(1).strip(" .,!")
            key = f"dislike:{value.lower()}"
            if key in seen:
                continue
            seen.add(key)
            items.append(
                PreferenceItem(
                    kind="dislike",
                    value=value,
                    sentiment="dislikes",
                    source_refs=[ref],
                    last_mentioned=ts,
                    confidence=0.75,
                )
            )
        for match in diet.finditer(text):
            value = match.group(1).strip()
            key = f"diet:{value.lower()}"
            if key in seen:
                continue
            seen.add(key)
            items.append(
                PreferenceItem(
                    kind="dietary_stated",
                    value=value,
                    sentiment="neutral",
                    source_refs=[ref],
                    last_mentioned=ts,
                    confidence=0.9,
                )
            )
        for match in list(named_place.finditer(text)) + list(praise.finditer(text)):
            value = match.group(1).strip(" .,!")
            # Stop at street / filler words.
            value = re.split(
                r"(?i)\s+on\s+|\s+—|\s+-|\s+in\s+the\b", value, maxsplit=1
            )[0].strip()
            if not _looks_like_place_name(value):
                continue
            key = f"place:{value.lower()}"
            if key in seen:
                for item in items:
                    if item.value.lower() == value.lower():
                        item.mention_count += 1
                        item.source_refs = list(
                            dict.fromkeys([*item.source_refs, ref])
                        )
                continue
            seen.add(key)
            kind = "cafe" if re.search(r"(?i)caf[eé]|coffee|beanery", value) else "restaurant"
            items.append(
                PreferenceItem(
                    kind=kind,  # type: ignore[arg-type]
                    value=value,
                    sentiment="likes",
                    source_refs=[ref],
                    last_mentioned=ts,
                    confidence=0.7,
                    mention_count=1,
                )
            )
        if snip.get("direction") == "event" and "@" in text:
            loc = text.split("@", 1)[-1].strip()
            if loc:
                key = f"hangout:{loc.lower()}"
                if key not in seen:
                    seen.add(key)
                    items.append(
                        PreferenceItem(
                            kind="hangout",
                            value=loc,
                            sentiment="likes",
                            source_refs=[ref],
                            last_mentioned=ts,
                            confidence=0.65,
                        )
                    )
        for match in cuisine.finditer(text):
            value = match.group(1).strip()
            # sushi often appears in dislikes — skip if already disliked
            if any(
                i.kind == "dislike" and value.lower() in i.value.lower() for i in items
            ):
                continue
            key = f"cuisine:{value.lower()}"
            if key in seen:
                # bump mention
                for item in items:
                    if item.value.lower() == value.lower():
                        item.mention_count += 1
                continue
            seen.add(key)
            sentiment: Any = "likes"
            kind: Any = "cuisine"
            if value.lower() == "sushi" and not_fan.search(text):
                kind = "dislike"
                sentiment = "dislikes"
            items.append(
                PreferenceItem(
                    kind=kind,
                    value=value,
                    sentiment=sentiment,
                    source_refs=[ref],
                    last_mentioned=ts,
                    confidence=0.65,
                )
            )
        for match in meet_at.finditer(text):
            value = match.group(1).strip(" .,!")
            if len(value) < 3:
                continue
            key = f"place:{value.lower()}"
            if key in seen:
                continue
            seen.add(key)
            items.append(
                PreferenceItem(
                    kind="hangout",
                    value=value,
                    sentiment="likes",
                    source_refs=[ref],
                    last_mentioned=ts,
                    confidence=0.55,
                )
            )

    # Merge duplicate place praise (mention_count).
    merged: dict[str, PreferenceItem] = {}
    for item in items:
        key = f"{item.kind}:{item.value.lower()}"
        if key in merged:
            merged[key].mention_count += item.mention_count
            merged[key].source_refs = list(
                dict.fromkeys([*merged[key].source_refs, *item.source_refs])
            )
            if item.last_mentioned > merged[key].last_mentioned:
                merged[key].last_mentioned = item.last_mentioned
        else:
            merged[key] = item
    return list(merged.values())


def _looks_like_place_name(value: str) -> bool:
    """Reject free-text fragments that are not venue-like."""
    text = (value or "").strip()
    if len(text) < 3 or len(text) > 48:
        return False
    lowered = text.lower()
    if lowered in {"the", "that", "this", "you", "him", "her"}:
        return False
    if re.search(r"(?i)\b(seeing|going|having|want|like|love)\b", text):
        return False
    # Require a capitalised token or an ampersand venue ("Cedar & Vine").
    if "&" in text:
        return True
    if re.search(r"(?i)\b(restaurant|café|cafe|kitchen|tavern|beanery|bar|bistro)\b", text):
        return True
    tokens = text.split()
    if len(tokens) >= 2 and all(t[:1].isupper() for t in tokens if t[0].isalpha()):
        return True
    return False


def _to_item(raw: _ExtractedItem) -> PreferenceItem:
    kind = raw.kind if raw.kind in {
        "cuisine", "restaurant", "cafe", "bar", "hangout",
        "neighbourhood", "dislike", "dietary_stated",
    } else "hangout"
    sentiment = raw.sentiment if raw.sentiment in {"likes", "dislikes", "neutral"} else "likes"
    return PreferenceItem(
        kind=kind,  # type: ignore[arg-type]
        value=raw.value.strip(),
        sentiment=sentiment,  # type: ignore[arg-type]
        source_refs=list(raw.source_refs),
        last_mentioned=raw.last_mentioned,
        confidence=float(raw.confidence),
    )


def _explicit_self_diet(item: PreferenceItem, snippets: list[dict[str, str]]) -> bool:
    for snip in snippets:
        if snip.get("source_ref") not in item.source_refs:
            continue
        if re.search(
            rf"(?i)\bI(?:'m| am)\s+{re.escape(item.value)}\b", snip.get("text") or ""
        ):
            return True
    return False


def _interactions_for_entity(
    entity: Any,
    *,
    layer: QueryLayer,
    settings: Settings,
) -> list[InteractionRow]:
    if entity is None:
        return []
    from recall.engines.world.relationship import (
        _entity_handle_set,
        _row_matches_handles,
    )

    handles = _entity_handle_set(entity)
    rows = all_interactions(
        source_tag="world.preferences.interactions",
        since_days=400,
        limit=5000,
        settings=settings,
        layer=layer,
    )
    return [r for r in rows if _row_matches_handles(r, handles)]


def _log_ledger(
    settings: Settings,
    run_id: str,
    *,
    entity_id: str,
    item_count: int,
    reason: str,
) -> None:
    path = Path(settings.logs_dir) / run_id / "preferences_ledger.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "entity_id": entity_id,
        "built_at": _now_s(settings),
        "item_count": item_count,
        "reason": reason,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _now_s(settings: Settings) -> str:
    return resolve_now(settings).strftime("%Y-%m-%dT%H:%M:%SZ")
