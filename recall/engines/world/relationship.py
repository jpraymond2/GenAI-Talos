"""Relationship labels between the user and each person (ingest + CLI)."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from recall.config import Settings, get_settings
from recall.data.queries import (
    InteractionRow,
    all_events_in_window,
    all_interactions,
    resolve_now,
)
from recall.data.query_layer import QueryLayer, get_query_layer
from recall.engines.world.models import (
    Relationship,
    RelationshipAssessment,
    RelationshipLabel,
    RelationshipSignal,
)
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
REASSESS_NEW_INTERACTIONS = 10
CONFIDENCE_FLOOR = 0.55
_PERSONAL_SOURCES = frozenset({"imessage", "whatsapp"})
_WORK_SOURCES = frozenset({"linkedin"})
_CORPORATE_TLDS = frozenset(
    {"com", "io", "co", "ai", "vc", "inc", "corp", "biz", "net", "org"}
)
_EDU_HINTS = ("edu", "ac.", "mail.")
_FAMILY_TERMS = re.compile(
    r"(?i)\b(mom|dad|mother|father|sister|brother|sis|bro|cousin|"
    r"aunt|uncle|grandma|grandpa|family|sibling)\b"
)
_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001F9FF"
    "\U00002600-\U000027BF"
    "\U0001F600-\U0001F64F"
    "]+",
    flags=re.UNICODE,
)
_SLANG_RE = re.compile(
    r"(?i)\b(lol|lmao|omg|tbh|idk|gonna|wanna|hey|yeah|yep|nah|btw|haha)\b"
)
_FORMAL_RE = re.compile(
    r"(?i)\b(dear\s+\w+|hello\s+(mr|ms|mrs|dr|prof)\b|best\s+regards|"
    r"kind\s+regards|sincerely|respectfully)\b"
)
_FIRST_NAME_HI = re.compile(r"(?i)^(hey|hi|hello)\s+[A-Z][a-z]+\b")

_ROOT = Path(__file__).resolve().parents[3]
_SCORE_ROOT = _ROOT / "data" / "relationship_scores"


class _LabelChoice(BaseModel):
    """LLM output for relationship classification."""

    label: RelationshipLabel = "acquaintance"
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    reasoning: str = ""


def classify_relationship(
    entity_id: str,
    *,
    store: WorldStore | None = None,
    settings: Settings | None = None,
    query_layer: QueryLayer | None = None,
    tracer: Tracer | None = None,
    run_id: str | None = None,
    force: bool = False,
) -> RelationshipAssessment:
    """Compute (or return cached override) relationship label for ``entity_id``."""
    active = settings if settings is not None else get_settings()
    world = store if store is not None else WorldStore(path=world_db_path(active), settings=active)
    layer = query_layer if query_layer is not None else get_query_layer(active, tracer=tracer)
    tr = tracer if tracer is not None else Tracer.current()
    rid = run_id or (tr.run_id if tr is not None else "rel-offline")

    prior = world.latest_relationship_assessment(entity_id)
    # User overrides are never replaced by automatic reassessment (ignore force).
    if prior is not None and prior.status == "user_override":
        return prior

    entity = world.get_entity(entity_id)
    if entity is None:
        return RelationshipAssessment(
            entity_id=entity_id,
            label="acquaintance",
            confidence=0.0,
            reasoning="Unknown entity.",
            scoring_version=SCORING_VERSION,
            assessed_at=_now_s(active),
            status="auto",
            run_id=rid,
        )

    rows = _interactions_for_entity(entity, layer=layer, settings=active)
    if prior is not None and not force:
        delta = len(rows) - int(prior.interaction_count_at_assess)
        if delta < REASSESS_NEW_INTERACTIONS and prior.status == "auto":
            return prior

    user_id = resolve_user_entity_id(world, settings=active, layer=layer)
    signals, snippets, shared_events = _compute_signals(
        entity, rows, layer=layer, settings=active
    )
    user_ent = world.get_entity(user_id) if user_id else None
    _enrich_shared_surname(
        signals,
        entity_name=entity.canonical_name,
        user_name=user_ent.canonical_name if user_ent else "",
    )
    choice = _choose_label_sync(
        signals, snippets, entity_name=entity.canonical_name, settings=active
    )
    label = choice.label
    confidence = float(choice.confidence)
    if confidence < CONFIDENCE_FLOOR:
        label = "acquaintance"

    assessed_at = _now_s(active)
    assessment = RelationshipAssessment(
        entity_id=entity_id,
        user_entity_id=user_id,
        label=label,
        confidence=round(confidence, 4),
        signals=signals,
        reasoning=choice.reasoning or _default_reasoning(label, signals),
        scoring_version=SCORING_VERSION,
        assessed_at=assessed_at,
        status="auto",
        interaction_count_at_assess=len(rows),
        run_id=rid,
    )
    score_path = _write_score_file(assessment)
    assessment.score_file = str(score_path)
    world.insert_relationship_assessment(assessment)
    _upsert_edge(world, assessment)
    _append_provenance(active, rid, assessment, shared_events)
    if tr is not None:
        tr.event(
            "world",
            "relationship",
            {
                "entity_id": entity_id,
                "label": label,
                "confidence": confidence,
                "score_file": assessment.score_file,
            },
        )
    return assessment


def set_relationship_override(
    entity_id: str,
    label: RelationshipLabel,
    *,
    store: WorldStore | None = None,
    settings: Settings | None = None,
    run_id: str | None = None,
) -> RelationshipAssessment:
    """User override: never replaced by automatic reassessment."""
    active = settings if settings is not None else get_settings()
    world = store if store is not None else WorldStore(path=world_db_path(active), settings=active)
    prior = world.latest_relationship_assessment(entity_id)
    user_id = prior.user_entity_id if prior else resolve_user_entity_id(world, settings=active)
    assessment = RelationshipAssessment(
        entity_id=entity_id,
        user_entity_id=user_id,
        label=label,
        confidence=1.0,
        signals=list(prior.signals) if prior else [],
        reasoning="User override.",
        scoring_version=SCORING_VERSION,
        assessed_at=_now_s(active),
        status="user_override",
        interaction_count_at_assess=(
            prior.interaction_count_at_assess if prior else 0
        ),
        run_id=run_id or "user-override",
    )
    score_path = _write_score_file(assessment)
    assessment.score_file = str(score_path)
    world.insert_relationship_assessment(assessment)
    _upsert_edge(world, assessment)
    return assessment


def resolve_user_entity_id(
    store: WorldStore,
    *,
    settings: Settings | None = None,
    layer: QueryLayer | None = None,
) -> str:
    """Pick the account-owner entity (fewest peer interaction hits among email holders)."""
    cached = store.get_meta("user_entity_id")
    if cached:
        return cached
    active = settings if settings is not None else get_settings()
    ql = layer if layer is not None else get_query_layer(active)
    interactions = all_interactions(
        source_tag="world.relationship.user",
        since_days=400,
        limit=5000,
        settings=active,
        layer=ql,
    )
    people = store.list_entities(type="person")
    peer_hits: dict[str, int] = {e.id: 0 for e in people}
    for entity in people:
        handles = _entity_handle_set(entity)
        for row in interactions:
            if _row_matches_handles(row, handles):
                peer_hits[entity.id] = peer_hits.get(entity.id, 0) + 1
    email_people = [e for e in people if any("@" in a for a in e.aliases)]
    pool = email_people or people
    if not pool:
        return ""
    chosen = min(pool, key=lambda e: (peer_hits.get(e.id, 0), e.canonical_name))
    store.set_meta("user_entity_id", chosen.id)
    return chosen.id


def assess_all_people(
    *,
    store: WorldStore,
    settings: Settings,
    query_layer: QueryLayer,
    tracer: Tracer | None = None,
    run_id: str,
) -> dict[str, int]:
    """Classify every person after ingest (skips fresh auto assessments)."""
    resolve_user_entity_id(store, settings=settings, layer=query_layer)
    scored = 0
    skipped = 0
    for entity in store.list_entities(type="person"):
        if entity.id == store.get_meta("user_entity_id"):
            skipped += 1
            continue
        prior = store.latest_relationship_assessment(entity.id)
        rows = _interactions_for_entity(entity, layer=query_layer, settings=settings)
        if prior is not None and prior.status == "user_override":
            skipped += 1
            continue
        if (
            prior is not None
            and prior.status == "auto"
            and len(rows) - prior.interaction_count_at_assess < REASSESS_NEW_INTERACTIONS
            and prior.scoring_version == SCORING_VERSION
        ):
            skipped += 1
            continue
        classify_relationship(
            entity.id,
            store=store,
            settings=settings,
            query_layer=query_layer,
            tracer=tracer,
            run_id=run_id,
            force=True,
        )
        scored += 1
    return {"scored": scored, "skipped": skipped}


def _choose_label_sync(
    signals: list[RelationshipSignal],
    snippets: list[dict[str, str]],
    *,
    entity_name: str,
    settings: Settings,
) -> _LabelChoice:
    """Call generate_json; fall back to heuristic when offline/unavailable."""
    heuristic = _heuristic_label(signals, snippets, entity_name=entity_name)
    if use_sample_provider(settings):
        # Sample cache rarely has this prompt; use deterministic heuristics.
        return heuristic
    payload = {
        "signals": [s.model_dump() for s in signals],
        "snippets": snippets[:15],
        "instruction": (
            "Classify the relationship type only; do not judge character, mood, "
            "or personality. Use family only if a family term or shared surname "
            "plus family context is explicit."
        ),
    }
    prompt = json.dumps(payload, ensure_ascii=False)
    system = (
        "You classify interpersonal relationship type for a meeting assistant. "
        "Return label in {social, professional, mixed, family, acquaintance}, "
        "confidence 0-1, and one-sentence reasoning."
    )
    try:
        import asyncio

        async def _run() -> _LabelChoice:
            result = await generate_json(
                prompt, system, _LabelChoice, role="memory_extract"
            )
            assert isinstance(result, _LabelChoice)
            return result

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None and loop.is_running():
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(lambda: asyncio.run(_run())).result(timeout=20)
        return asyncio.run(_run())
    except (LLMUnavailable, LLMOutputError, TimeoutError, Exception) as exc:  # noqa: BLE001
        logger.info("relationship LLM fallback: %s", exc)
        return heuristic


def _heuristic_label(
    signals: list[RelationshipSignal],
    snippets: list[dict[str, str]],
    *,
    entity_name: str,
) -> _LabelChoice:
    by_name = {s.name: s.value for s in signals}
    personal = float(by_name.get("channel_personal_share", 0) or 0)
    work = float(by_name.get("channel_work_share", 0) or 0)
    informal = float(by_name.get("informality_share", 0) or 0)
    evening = float(by_name.get("evening_weekend_share", 0) or 0)
    family_hits = int(by_name.get("family_term_hits", 0) or 0)
    shared_surname = bool(by_name.get("shared_surname", 0))
    text_blob = " ".join(s.get("text", "") for s in snippets)

    if family_hits > 0 or (shared_surname and _FAMILY_TERMS.search(text_blob)):
        return _LabelChoice(
            label="family",
            confidence=0.85 if family_hits else 0.7,
            reasoning="Family terms or shared surname with family context appear in messages.",
        )
    # Friend + work (founder/colleague who also texts): personal and work both material.
    if personal >= 0.4 and work >= 0.25:
        return _LabelChoice(
            label="mixed",
            confidence=0.72 if informal >= 0.2 else 0.65,
            reasoning="Personal and work channels both appear regularly.",
        )
    if personal >= 0.55 and work < 0.25:
        return _LabelChoice(
            label="social",
            confidence=0.7 + min(0.15, evening * 0.2),
            reasoning="Most contact is on personal channels.",
        )
    if work >= 0.5 or (work >= 0.35 and personal < 0.25 and informal < 0.25):
        return _LabelChoice(
            label="professional",
            confidence=0.68,
            reasoning="Contact is mostly on work channels with formal tone.",
        )
    if personal < 0.25 and work < 0.25:
        return _LabelChoice(
            label="acquaintance",
            confidence=0.6,
            reasoning="Sparse or ambiguous contact pattern.",
        )
    if personal >= 0.4:
        return _LabelChoice(
            label="social",
            confidence=0.62,
            reasoning="Personal-channel contact dominates.",
        )
    return _LabelChoice(
        label="acquaintance",
        confidence=0.5,
        reasoning=f"Unclear mix for {entity_name}; defaulting to acquaintance.",
    )


def _compute_signals(
    entity: Any,
    rows: list[InteractionRow],
    *,
    layer: QueryLayer,
    settings: Settings,
) -> tuple[list[RelationshipSignal], list[dict[str, str]], list[dict[str, Any]]]:
    now = resolve_now(settings)
    window_90 = now - timedelta(days=90)
    window_180 = now - timedelta(days=180)

    def _ts(row: InteractionRow) -> datetime:
        try:
            return datetime.fromisoformat(row.ts_utc.replace("Z", "+00:00"))
        except ValueError:
            return now

    recent_90 = [r for r in rows if _ts(r) >= window_90]
    recent_180 = [r for r in rows if _ts(r) >= window_180]
    refs_all = [r.source_ref for r in rows if r.source_ref]

    personal_n = sum(1 for r in rows if r.source in _PERSONAL_SOURCES)
    work_n = sum(1 for r in rows if _is_work_channel(r))
    total = max(len(rows), 1)
    outbound = sum(1 for r in rows if r.direction == "out")
    inbound = sum(1 for r in rows if r.direction == "in")

    informal_n = 0
    formal_n = 0
    evening_n = 0
    family_hits = 0
    for r in rows:
        text = f"{r.subject or ''} {r.text or ''}"
        if _EMOJI_RE.search(text) or _SLANG_RE.search(text) or _FIRST_NAME_HI.search(text.strip()):
            informal_n += 1
        if _FORMAL_RE.search(text):
            formal_n += 1
        if _FAMILY_TERMS.search(text):
            family_hits += 1
        ts = _ts(r)
        if ts.weekday() >= 5 or ts.hour < 8 or ts.hour >= 18:
            evening_n += 1

    days_since = 999
    if rows:
        last = max(_ts(r) for r in rows)
        days_since = max(0, (now - last).days)

    events = all_events_in_window(
        source_tag="world.relationship.events",
        days_back=400,
        days_forward=60,
        settings=settings,
        layer=layer,
    )
    emails = {a.lower() for a in entity.aliases if "@" in a and "whatsapp" not in a}
    shared = [
        e.model_dump()
        for e in events
        if any(att.lower() in emails for att in e.attendees)
    ]
    shared_refs = [str(e.get("source_ref") or e.get("event_id") or "") for e in shared]

    user_id = ""  # surname check vs user filled by caller via meta
    # Shared surname: compare last tokens with user entity when available.
    shared_surname = 0
    # Filled below if we can load user from store — skip here; signal set by caller path.

    signals = [
        RelationshipSignal(
            name="channel_personal_share",
            value=round(personal_n / total, 4),
            source_refs=refs_all[:8],
        ),
        RelationshipSignal(
            name="channel_work_share",
            value=round(work_n / total, 4),
            source_refs=refs_all[:8],
        ),
        RelationshipSignal(
            name="interactions_90d",
            value=len(recent_90),
            source_refs=[r.source_ref for r in recent_90[:5]],
        ),
        RelationshipSignal(
            name="days_since_last_contact",
            value=days_since,
            source_refs=refs_all[:1],
        ),
        RelationshipSignal(
            name="outbound_share",
            value=round(outbound / total, 4),
            source_refs=refs_all[:5],
        ),
        RelationshipSignal(
            name="inbound_share",
            value=round(inbound / total, 4),
            source_refs=refs_all[:5],
        ),
        RelationshipSignal(
            name="informality_share",
            value=round(informal_n / total, 4),
            source_refs=refs_all[:5],
        ),
        RelationshipSignal(
            name="formality_share",
            value=round(formal_n / total, 4),
            source_refs=refs_all[:5],
        ),
        RelationshipSignal(
            name="evening_weekend_share",
            value=round(evening_n / total, 4),
            source_refs=refs_all[:5],
        ),
        RelationshipSignal(
            name="shared_events",
            value=len(shared),
            source_refs=shared_refs[:5],
        ),
        RelationshipSignal(
            name="family_term_hits",
            value=family_hits,
            source_refs=[r.source_ref for r in rows if _FAMILY_TERMS.search(r.text or "")][:5],
        ),
        RelationshipSignal(
            name="shared_surname",
            value=shared_surname,
            source_refs=[],
        ),
    ]

    # Mixed sources, short snippets from last 180 days.
    snippets: list[dict[str, str]] = []
    by_source: dict[str, list[InteractionRow]] = {}
    for r in sorted(recent_180, key=lambda x: x.ts_utc, reverse=True):
        by_source.setdefault(r.source, []).append(r)
    for source in ("whatsapp", "imessage", "gmail", "linkedin"):
        for r in by_source.get(source, [])[:5]:
            text = (r.text or r.subject or "").strip()
            if not text:
                continue
            snippets.append(
                {
                    "source": r.source,
                    "ts": r.ts_utc,
                    "text": text[:180],
                    "source_ref": r.source_ref,
                }
            )
            if len(snippets) >= 15:
                break
        if len(snippets) >= 15:
            break

    return signals, snippets, shared


def _enrich_shared_surname(
    signals: list[RelationshipSignal],
    *,
    entity_name: str,
    user_name: str,
) -> None:
    ent_last = entity_name.strip().rsplit(" ", 1)[-1].lower() if entity_name else ""
    user_last = user_name.strip().rsplit(" ", 1)[-1].lower() if user_name else ""
    shared = int(bool(ent_last and user_last and ent_last == user_last and len(ent_last) > 2))
    for sig in signals:
        if sig.name == "shared_surname":
            sig.value = shared
            return
    signals.append(RelationshipSignal(name="shared_surname", value=shared, source_refs=[]))


def _is_work_channel(row: InteractionRow) -> bool:
    if row.source in _WORK_SOURCES:
        return True
    if row.source != "gmail":
        return False
    handle = (row.handle_value or "").lower()
    if "@" not in handle:
        return False
    domain = handle.rsplit("@", 1)[-1]
    if any(h in domain for h in _EDU_HINTS):
        return False
    # Personal webmail
    if domain in {"gmail.com", "googlemail.com", "yahoo.com", "outlook.com", "hotmail.com", "icloud.com", "me.com", "example.com"}:
        return False
    tld = domain.rsplit(".", 1)[-1]
    return tld in _CORPORATE_TLDS or "." in domain


def _interactions_for_entity(
    entity: Any,
    *,
    layer: QueryLayer,
    settings: Settings,
) -> list[InteractionRow]:
    handles = _entity_handle_set(entity)
    rows = all_interactions(
        source_tag="world.relationship.interactions",
        since_days=400,
        limit=5000,
        settings=settings,
        layer=layer,
    )
    return [r for r in rows if _row_matches_handles(r, handles)]


def _entity_handle_set(entity: Any) -> set[str]:
    values = {a for a in entity.aliases}
    phones = {re.sub(r"\D", "", a) for a in entity.aliases if a.startswith("+") or a.isdigit()}
    for a in list(values):
        if "@" in a and a.split("@", 1)[0].replace("+", "").isdigit():
            phones.add(re.sub(r"\D", "", a.split("@", 1)[0]))
    values |= phones
    return {v.lower() if isinstance(v, str) and not v.isdigit() else v for v in values}


def _row_matches_handles(row: InteractionRow, handles: set[str]) -> bool:
    hv = row.handle_value
    if hv in handles or hv.lower() in handles:
        return True
    digits = re.sub(r"\D", "", hv.split("@", 1)[0])
    return bool(digits and digits in handles)


def _upsert_edge(store: WorldStore, assessment: RelationshipAssessment) -> None:
    if not assessment.user_entity_id:
        return
    rel_id = "rel_" + hashlib.sha1(
        f"{assessment.user_entity_id}|{assessment.entity_id}|label".encode()
    ).hexdigest()[:12]
    refs = []
    for sig in assessment.signals:
        refs.extend(sig.source_refs[:2])
    store.upsert_relationship(
        Relationship(
            id=rel_id,
            from_id=assessment.user_entity_id,
            to_id=assessment.entity_id,
            type=assessment.label,  # type: ignore[arg-type]
            valid_from=assessment.assessed_at[:10],
            source_ref="|".join(refs[:6]),
            confidence=assessment.confidence,
        )
    )


def _write_score_file(assessment: RelationshipAssessment) -> Path:
    folder = _SCORE_ROOT / assessment.entity_id
    folder.mkdir(parents=True, exist_ok=True)
    run = assessment.run_id or "offline"
    path = folder / f"{run}.json"
    path.write_text(
        json.dumps(assessment.model_dump(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path


def _append_provenance(
    settings: Settings,
    run_id: str,
    assessment: RelationshipAssessment,
    shared_events: list[dict[str, Any]],
) -> None:
    path = Path(settings.logs_dir) / run_id / "world_provenance.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "kind": "relationship_assessment",
        "entity_id": assessment.entity_id,
        "label": assessment.label,
        "confidence": assessment.confidence,
        "status": assessment.status,
        "score_file": assessment.score_file,
        "shared_events": len(shared_events),
        "ts": assessment.assessed_at,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _default_reasoning(label: RelationshipLabel, signals: list[RelationshipSignal]) -> str:
    personal = next((s.value for s in signals if s.name == "channel_personal_share"), 0)
    return f"Labeled {label} from channel and tone signals (personal_share={personal})."


def _now_s(settings: Settings) -> str:
    return resolve_now(settings).strftime("%Y-%m-%dT%H:%M:%SZ")

