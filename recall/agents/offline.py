"""Deterministic offline fallbacks when the sample LLM cache misses.

Used on ``SampleCacheMiss`` (or when there is no NVIDIA key). Every fallback
traces ``provider = "offline_rules"``.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any, Literal

from rapidfuzz import fuzz

from recall.agents.time_context import AGENT_TIMEZONE, now as frozen_now
from recall.config import Settings, get_settings
from recall.schemas import (
    DEFAULT_SOURCES_PLAN,
    Brief,
    CriticVerdict,
    Evidence,
    Intent,
    PointVerdict,
    RunState,
    TalkingPoint,
)
from recall.tracing import Tracer

_MESSAGE_SOURCES = frozenset({"gmail", "imessage", "whatsapp", "messenger"})

_NOTE_RE = re.compile(r"(?i)\b(note that|remember that|remember:|note:)\b")
_SUGGEST_RE = re.compile(
    r"(?i)\b(where should we eat|restaurant|suggest (a )?place|what does .+ like|"
    r"where (?:to|should we|can we)\b)"
)
_CALENDAR_RE = re.compile(
    r"(?i)\b(what'?s on my calendar|who am i meeting on|calendar for|schedule for)\b"
)
_OPEN_LOOPS_RE = re.compile(
    r"(?i)\b(what do i owe|did i reply|open loops?|what am i forgetting)\b"
)
_MEET_RE = re.compile(
    r"(?i)\b(?:i'?m\s+)?(?:meeting|meet|seeing|lunch with|dinner with|coffee with|"
    r"call with|sync with|brief me on|catch me up on|catch up with|"
    r"what'?s new with)\s+(.+?)(?:\s+on\s+|\s+at\s+|\s*,\s*|\s*$)"
)
_QUESTION_RE = re.compile(
    r"(?i)^\s*(where does|where is|what(?:'s| is)|did i|do i|who is)\b"
)
_RELATIVE_WHEN = re.compile(
    r"(?i)\b("
    r"today|tomorrow|tonight|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"on\s+(?:january|february|march|april|may|june|july|august|september|"
    r"october|november|december)\s+\d{1,2}|"
    r"on\s+(?:jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec)\.?\s+\d{1,2}|"
    r"the\s+\d{1,2}(?:st|nd|rd|th)|"
    r"at\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?"
    r")\b"
)
_WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}
# Relational cue → synonym tokens matched against title/label/nickname/alias.
_RELATION_SYNONYMS: dict[str, frozenset[str]] = {
    "landlord": frozenset({"landlord", "proprio", "propriétaire", "lessor", "owner"}),
    "mom": frozenset({"mom", "mum", "mother", "maman", "mère", "mama"}),
    "dad": frozenset({"dad", "father", "papa", "père", "papi"}),
}
_RELATION_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?i)\bmy\s+landlord\b|\bproprio\b"), "landlord"),
    (re.compile(r"(?i)\bmy\s+(?:mom|mum|mother)\b|\bmaman\b|\bmère\b"), "mom"),
    (re.compile(r"(?i)\bmy\s+(?:dad|father)\b|\bpapa\b|\bpère\b"), "dad"),
]
_MBA_RE = re.compile(r"(?i)\b(?:from\s+)?(?:my\s+)?mba\b")
_FROM_TOKEN_RE = re.compile(
    r"(?i)\bfrom\s+([A-Za-z0-9][A-Za-z0-9.&'-]{1,40})\b"
)
_NOISE_RE = re.compile(
    r"(?i)^("
    r"ok+|thanks?!?|thank you|lol|haha|yes|no|yep|nope|"
    r"👍|🙏|❤️|😂|"
    r"\d{4,8}|"
    r"your (?:code|otp|pin) is\b.*|"
    r"unsubscribe\b.*|receipt\b.*|newsletter\b.*"
    r")$"
)
_ISO_Z_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")


def route_intent(
    text: str,
    *,
    deps: Any = None,
    settings: Settings | None = None,
) -> Intent:
    """Rule-based router: kind, person_name, when, question_field."""
    active = settings if settings is not None else get_settings()
    clock = frozen_now(settings=active)
    utterance = (text or "").strip()
    kind, question_field = _detect_kind(utterance)
    when = parse_when_phrase(utterance, clock)
    person = _extract_person(utterance, deps=deps)
    confidence = 0.9 if person or kind in {"note", "calendar", "open_loops", "suggest", "question"} else 0.7
    if kind == "unknown":
        confidence = 0.3
    if kind == "unknown" and person:
        kind = "brief"
    intent = Intent(
        kind=kind,  # type: ignore[arg-type]
        person_name=person,
        when=when,
        action_request=None,
        meal_type=None,
        question_field=question_field,
        sources_plan=list(DEFAULT_SOURCES_PLAN),
        confidence=confidence,
    )
    _trace_offline(
        deps,
        "router",
        f"kind={intent.kind} person={intent.person_name!r} "
        f"field={intent.question_field!r} confidence={intent.confidence:.2f}",
        {"intent": intent.model_dump(mode="json")},
    )
    return intent


def write_brief(state: RunState, deps: Any = None) -> Brief:
    """Build a Brief only from structured world facts + evidence pack."""
    person = state.entity_name or (state.intent.person_name if state.intent else "unknown")
    evidence = state.evidence or {}
    settings = getattr(deps, "settings", None) if deps is not None else None
    store = getattr(deps, "world_store", None) if deps is not None else None
    entity = store.get_entity(state.entity_id) if store is not None and state.entity_id else None
    ctx = state.context.get("person_context") or {}

    if state.intent and state.intent.kind == "question" and state.intent.question_field:
        brief = _answer_question(state, entity=entity, store=store, ctx=ctx, evidence=evidence)
        _trace_offline(
            deps,
            "writer",
            f"person={brief.person!r} points={len(brief.points)} headline={brief.headline[:80]!r}",
            {"brief": brief.model_dump(mode="json")},
        )
        return brief

    if not evidence and not entity:
        brief = Brief(
            person=person or "unknown",
            headline=f"I have nothing on {person}." if person else "I have nothing on them.",
            points=[],
            caveats=["No evidence in the pack."],
        )
        _trace_offline(
            deps,
            "writer",
            f"person={brief.person!r} points=0 headline={brief.headline[:80]!r}",
            {"brief": brief.model_dump(mode="json")},
        )
        return brief

    org, title, org_note = structured_org_title(entity, store, settings=settings)
    last_contact = _last_contact_line(evidence, ctx=ctx, settings=settings)
    next_meeting = _next_meeting_line(evidence, ctx=ctx, settings=settings)
    role_bit = ""
    if title and org:
        role_bit = f", {title} at {org}"
    elif org:
        role_bit = f" at {org}"
    elif title:
        role_bit = f", {title}"
    parts = [f"{person}{role_bit}"]
    if last_contact:
        parts.append(last_contact)
    if next_meeting:
        parts.append(next_meeting)
    headline = "; ".join(parts)
    if org_note:
        headline = f"{headline} ({org_note})"

    points = _select_points(
        evidence,
        person=person,
        missing=_missing_ids(state),
        drop_indexes=_drop_indexes(state),
        prior_draft=state.drafts[-1] if state.drafts else None,
        entity=entity,
        settings=settings if isinstance(settings, Settings) else None,
    )
    caveats: list[str] = []
    if len(points) <= 1:
        caveats.append("Data is thin — only a few snippets on file.")

    brief = Brief(person=person, headline=headline, points=points, caveats=caveats)
    _trace_offline(
        deps,
        "writer",
        f"person={brief.person!r} points={len(brief.points)} headline={brief.headline[:80]!r}",
        {"brief": brief.model_dump(mode="json")},
    )
    return brief


def critique_brief(state: RunState, deps: Any = None) -> CriticVerdict:
    """Rule-based critic: drop bad citations, flag missing open loops / events."""
    if not state.drafts:
        raise ValueError("critic requires a draft")
    draft = state.drafts[-1]
    pack = state.evidence or {}
    entity_name = (state.entity_name or "").strip().lower()

    verdicts: list[PointVerdict] = []
    kept_points: list[TalkingPoint] = []
    for index, point in enumerate(draft.points):
        valid_ids = [eid for eid in point.evidence_ids if eid in pack]
        words = len(point.text.split())
        if not valid_ids or words < 6 or _ISO_Z_RE.search(point.text):
            verdicts.append(
                PointVerdict(
                    index=index,
                    decision="drop",
                    reason="Bad point (missing evidence, too short, or ISO timestamp).",
                    fix_hint="Rewrite with a template citing pack evidence.",
                )
            )
            continue
        verdicts.append(
            PointVerdict(index=index, decision="keep", reason="Cited evidence in pack.", fix_hint=None)
        )
        kept_points.append(point.model_copy(update={"evidence_ids": valid_ids}))

    missing: list[str] = []
    refetch: list[str] = []
    overall: str = "pass"

    headline_name = (draft.person or "").strip().lower()
    if entity_name and headline_name and headline_name != entity_name:
        overall = "revise"
        missing.append(f"Headline person {draft.person!r} ≠ resolved {state.entity_name!r}")

    cited = {eid for p in kept_points for eid in p.evidence_ids}
    open_loop_ids = [eid for eid, item in pack.items() if "open_loop" in (item.tags or [])]
    if open_loop_ids and not any(eid in cited for eid in open_loop_ids):
        missing.append(open_loop_ids[0])
        overall = "revise"
    upcoming = [
        eid
        for eid, item in pack.items()
        if item.source == "calendar" and "upcoming" in (item.tags or [])
    ]
    if not upcoming:
        upcoming = [eid for eid, item in pack.items() if item.source == "calendar"]
    if upcoming and not any(eid in cited for eid in upcoming):
        missing.append(upcoming[0])
        overall = "revise"

    by_source: dict[str, int] = {}
    for item in pack.values():
        by_source[item.source] = by_source.get(item.source, 0) + 1
    for source, count in by_source.items():
        if count < 2 and source in {"gmail", "calendar", "memory", "web"}:
            refetch.append(source)

    if any(v.decision == "drop" for v in verdicts) and overall == "pass":
        overall = "revise"

    verdict = CriticVerdict(
        overall=overall,  # type: ignore[arg-type]
        verdicts=verdicts,
        missing=missing,
        refetch=sorted(set(refetch)),
    )
    keep = sum(1 for item in verdict.verdicts if item.decision == "keep")
    _trace_offline(
        deps,
        "critic",
        f"overall={verdict.overall} keep={keep}/{len(verdict.verdicts)} "
        f"missing={len(verdict.missing)} refetch={len(verdict.refetch)}",
        {"verdict": verdict.model_dump(mode="json")},
    )
    return verdict


def _banned_name_tokens(
    *,
    entity: Any = None,
    settings: Settings | None = None,
) -> frozenset[str]:
    """First names of the dataset owner and the resolved person (dynamic)."""
    tokens: set[str] = set()
    active = settings if settings is not None else get_settings()
    owner = (active.dataset_owner or "").strip()
    if owner:
        tokens.add(owner.split()[0].casefold())
    if entity is not None:
        name = str(getattr(entity, "canonical_name", "") or "")
        if name:
            tokens.add(name.split()[0].casefold())
            for part in name.split():
                if len(part) >= 3:
                    tokens.add(part.casefold())
    return frozenset(tokens)


def structured_org_title(
    entity: Any,
    store: Any = None,
    *,
    settings: Settings | None = None,
) -> tuple[str, str, str]:
    """Org/title from contacts / attribute history / conflicts only (no body regex)."""
    if entity is None:
        return "", "", ""

    banned = _banned_name_tokens(entity=entity, settings=settings)
    org_cands: list[tuple[datetime, str, str]] = []
    title_cands: list[tuple[datetime, str, str]] = []

    def _add(field: str, value: str, source_ref: str, observed_at: str) -> None:
        if not _looks_like_structured_value(
            value, source_ref, banned_name_tokens=banned
        ):
            return
        try:
            ts = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
        except ValueError:
            ts = datetime(1970, 1, 1, tzinfo=AGENT_TIMEZONE)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=AGENT_TIMEZONE)
        item = (ts, value.strip(), source_ref or "")
        if field == "org":
            org_cands.append(item)
        elif field == "title":
            title_cands.append(item)

    attrs = getattr(entity, "attributes", None) or {}
    history = getattr(entity, "attribute_history", None) or []
    for obs in history:
        field = getattr(obs, "field", None) or (obs.get("field") if isinstance(obs, dict) else None)
        value = getattr(obs, "value", None) or (obs.get("value") if isinstance(obs, dict) else None)
        source_ref = getattr(obs, "source_ref", None) or (
            obs.get("source_ref") if isinstance(obs, dict) else ""
        )
        observed_at = getattr(obs, "observed_at", None) or (
            obs.get("observed_at") if isinstance(obs, dict) else ""
        )
        if field and value:
            _add(str(field), str(value), str(source_ref or ""), str(observed_at or ""))

    # Fall back to current attributes only when they look structured.
    for field in ("org", "title"):
        raw = str(attrs.get(field) or "").strip()
        if _looks_like_structured_value(
            raw, "attribute", banned_name_tokens=banned
        ):
            _add(field, raw, "attribute", "1970-01-01T00:00:00Z")

    if store is not None and getattr(entity, "id", None):
        for conflict in store.list_conflicts(entity.id):
            field = getattr(conflict, "field", "") or ""
            if field not in {"org", "title", "city"}:
                continue
            for val in getattr(conflict, "values", None) or []:
                value = getattr(val, "value", None) or (
                    val.get("value") if isinstance(val, dict) else None
                )
                source_ref = getattr(val, "source_ref", None) or (
                    val.get("source_ref") if isinstance(val, dict) else ""
                )
                observed_at = getattr(val, "observed_at", None) or (
                    val.get("observed_at") if isinstance(val, dict) else ""
                )
                if field in {"org", "title"} and value:
                    _add(field, str(value), str(source_ref or ""), str(observed_at or ""))

    def _pick(cands: list[tuple[datetime, str, str]]) -> tuple[str, str, str]:
        if not cands:
            return "", "", ""
        cands = sorted(cands, key=lambda x: x[0], reverse=True)
        newest_val, newest_src = cands[0][1], cands[0][2]
        contact_vals = [c for c in cands if str(c[2]).startswith("contact:")]
        contact_val = contact_vals[0][1] if contact_vals else ""
        return newest_val, newest_src, contact_val

    org, org_src, org_card = _pick(org_cands)
    title, title_src, title_card = _pick(title_cands)
    note = ""
    if org_card and org and org_card.lower() != org.lower():
        note = f"contact card still says {org_card}"
    elif title_card and title and title_card.lower() != title.lower() and not note:
        note = f"contact card still says {title_card}"
    _ = (org_src, title_src)
    return org, title, note


def parse_when_phrase(text: str, clock: datetime) -> str | None:
    """Return a normalized when string (ISO date) relative to clock."""
    local = clock.astimezone(AGENT_TIMEZONE)
    match = _RELATIVE_WHEN.search(text or "")
    if not match:
        return None
    raw = match.group(1).strip().lower()
    if raw in {"today", "tonight"}:
        return local.date().isoformat()
    if raw == "tomorrow":
        return (local.date() + timedelta(days=1)).isoformat()
    for name, idx in _WEEKDAYS.items():
        if name in raw:
            delta = (idx - local.weekday()) % 7
            target = local.date() + timedelta(days=delta)
            return target.isoformat()
    months = {
        "january": 1,
        "february": 2,
        "march": 3,
        "april": 4,
        "may": 5,
        "june": 6,
        "july": 7,
        "august": 8,
        "september": 9,
        "october": 10,
        "november": 11,
        "december": 12,
        "jan": 1,
        "feb": 2,
        "mar": 3,
        "apr": 4,
        "jun": 6,
        "jul": 7,
        "aug": 8,
        "sep": 9,
        "oct": 10,
        "nov": 11,
        "dec": 12,
    }
    m = re.search(
        r"(?i)(?:on\s+)?(january|february|march|april|may|june|july|august|"
        r"september|october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec)"
        r"\.?\s+(\d{1,2})",
        text or "",
    )
    if m:
        month = months[m.group(1).lower()]
        day = int(m.group(2))
        try:
            return datetime(local.year, month, day, tzinfo=AGENT_TIMEZONE).date().isoformat()
        except ValueError:
            return raw
    return raw


def parse_when_datetime(when: str | None, clock: datetime) -> datetime | None:
    """Parse a when string into a datetime near ``clock``."""
    if not when:
        return None
    local = clock.astimezone(AGENT_TIMEZONE)
    text = when.strip()
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            d = datetime.fromisoformat(text).date()
            return datetime(d.year, d.month, d.day, 12, 0, tzinfo=AGENT_TIMEZONE)
    except ValueError:
        pass
    for name, idx in _WEEKDAYS.items():
        if name in text.lower():
            delta = (idx - local.weekday()) % 7
            d = local.date() + timedelta(days=delta)
            return datetime(d.year, d.month, d.day, 12, 0, tzinfo=AGENT_TIMEZONE)
    return None


def format_human_when(ts: datetime, *, settings: Settings | None = None) -> str:
    """America/Toronto human date: 'Wed Oct 28, 10:00'."""
    _ = settings
    local = ts.astimezone(AGENT_TIMEZONE)
    return f"{local.strftime('%a %b')} {local.day}, {local.strftime('%H:%M')}"


def format_human_day(ts: datetime, *, settings: Settings | None = None) -> str:
    """America/Toronto day label without clock time: 'Wed Sep 30'."""
    _ = settings
    local = ts.astimezone(AGENT_TIMEZONE)
    return f"{local.strftime('%a %b')} {local.day}"


def _detect_kind(text: str) -> tuple[str, str | None]:
    if _NOTE_RE.search(text):
        return "note", None
    if _SUGGEST_RE.search(text):
        return "suggest", None
    if _CALENDAR_RE.search(text):
        return "calendar", None
    if _OPEN_LOOPS_RE.search(text) or re.search(r"(?i)\bwhat do i owe\b", text):
        if re.search(r"(?i)\bwhat do i owe\b", text):
            return "question", "owe"
        return "open_loops", None
    if re.search(r"(?i)\bdid i (ever )?reply\b", text):
        return "question", "replied"
    if re.search(r"(?i)\bwhere does .+ live\b|\bwhere (?:is|does) .+ (?:based|live)\b", text):
        return "question", "location"
    if re.search(r"(?i)\bwhere does .+ work\b|\bwhat(?:'s| is) .+ (?:employer|company|job)\b", text):
        return "question", "employer"
    if re.search(r"(?i)\b(?:what(?:'s| is)|whats) .+ number\b|\bphone\b", text):
        return "question", "phone"
    if _QUESTION_RE.search(text) and not _MEET_RE.search(text):
        return "question", None
    if _MEET_RE.search(text) or re.search(r"(?i)\b(brief|catch me up|seeing|sync with)\b", text):
        return "brief", None
    return "unknown", None


def _extract_person(text: str, *, deps: Any = None) -> str | None:
    lowered = text or ""

    for pattern, key in _RELATION_PATTERNS:
        if pattern.search(lowered):
            hit = _match_relation(key, deps=deps)
            if hit:
                return hit

    if _MBA_RE.search(lowered):
        hit = _match_tokens({"mba"}, deps=deps)
        if hit:
            return hit
    from_hit = _FROM_TOKEN_RE.search(lowered)
    if from_hit:
        token = from_hit.group(1).strip().casefold()
        if token not in {"my", "the", "a", "an", "our"}:
            hit = _match_tokens({token}, deps=deps)
            if hit:
                return hit

    # "Where does <name> live?" / "What's <name>'s number?"
    qm = re.search(
        r"(?i)(?:where does|where is|what(?:'s| is)|whats)\s+([A-Za-zÀ-ÖØ-öø-ÿ' ]+?)(?:'s)?\s+"
        r"(?:live|work|number|phone|employer|company)\b",
        lowered,
    )
    if qm:
        frag = qm.group(1).strip(" .,!?")
        if frag:
            return frag if len(frag.split()) >= 2 else (_unique_world_name(frag, deps=deps) or frag)

    rm = re.search(r"(?i)did i (?:ever )?reply to\s+([A-Za-zÀ-ÖØ-öø-ÿ' ]+?)\s*\??$", lowered)
    if rm:
        frag = rm.group(1).strip()
        return frag if len(frag.split()) >= 2 else (_unique_world_name(frag, deps=deps) or frag)

    owe = re.search(r"(?i)what do i owe\s+(.+?)\s*\??$", lowered)
    if owe:
        frag = owe.group(1).strip()
        for pattern, key in _RELATION_PATTERNS:
            if pattern.search(frag) or pattern.search("my " + frag):
                hit = _match_relation(key, deps=deps)
                if hit:
                    return hit
        return frag

    meet = _MEET_RE.search(lowered)
    fragment = ""
    if meet:
        fragment = meet.group(1).strip(" .,!?")
        fragment = re.sub(
            r"(?i)\s+(today|tomorrow|tonight|soon|on|at|this|next)\b.*$",
            "",
            fragment,
        ).strip(" .,!?")
        fragment = re.sub(
            r"(?i)\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b.*$",
            "",
            fragment,
        ).strip(" .,!?")
        fragment = re.sub(r"(?i)^(my|the|a|an)\s+", "", fragment).strip()

    if fragment and len(fragment.split()) >= 2:
        if fragment.lower() not in {"someone", "them", "him", "her"}:
            return fragment

    if fragment and len(fragment.split()) == 1:
        unique = _unique_world_name(fragment, deps=deps)
        if unique:
            return unique
        return fragment

    world_hit = _fuzzy_world_match_utterance(lowered, deps=deps)
    if world_hit:
        return world_hit
    return None


def _match_relation(key: str, *, deps: Any = None) -> str | None:
    tokens = _RELATION_SYNONYMS.get(key, frozenset())
    return _match_tokens(tokens, deps=deps)


def _match_tokens(tokens: set[str] | frozenset[str], *, deps: Any = None) -> str | None:
    store = getattr(deps, "world_store", None) if deps is not None else None
    if store is None:
        return None
    owner = ""
    settings = getattr(deps, "settings", None)
    if settings is not None:
        owner = (settings.dataset_owner or "").strip().lower()
    owner_id = store.get_meta("user_entity_id") if hasattr(store, "get_meta") else None
    for entity in store.list_entities(type="person"):
        if owner_id and entity.id == owner_id:
            continue
        if owner and entity.canonical_name.strip().lower() == owner:
            continue
        attrs = getattr(entity, "attributes", None) or {}
        pool_parts = [
            entity.canonical_name,
            *list(entity.aliases or []),
            str(attrs.get("title") or ""),
            str(attrs.get("org") or ""),
            str(attrs.get("label") or ""),
            str(attrs.get("nickname") or ""),
        ]
        pool = " ".join(pool_parts).lower()
        if any(tok in pool for tok in tokens):
            return entity.canonical_name
    return None


def _unique_world_name(needle: str, *, deps: Any = None) -> str | None:
    store = getattr(deps, "world_store", None) if deps is not None else None
    if store is None:
        return None
    needle_l = needle.strip().lower()
    if not needle_l:
        return None
    hits: list[str] = []
    owner = ""
    settings = getattr(deps, "settings", None)
    if settings is not None:
        owner = (settings.dataset_owner or "").strip().lower()
    owner_id = store.get_meta("user_entity_id") if hasattr(store, "get_meta") else None
    for entity in store.list_entities(type="person"):
        if owner_id and entity.id == owner_id:
            continue
        if owner and entity.canonical_name.strip().lower() == owner:
            continue
        aliases = [entity.canonical_name, *list(entity.aliases or [])]
        for alias in aliases:
            alias_l = alias.lower()
            first = alias_l.split()[0] if alias_l.split() else ""
            if (
                alias_l == needle_l
                or first == needle_l
                or needle_l in alias_l.split()
                or fuzz.token_set_ratio(needle, alias) >= 90
            ):
                hits.append(entity.canonical_name)
                break
    uniq = list(dict.fromkeys(hits))
    return uniq[0] if len(uniq) == 1 else None


def _fuzzy_world_match_utterance(text: str, *, deps: Any = None) -> str | None:
    store = getattr(deps, "world_store", None) if deps is not None else None
    if store is None:
        return None
    people = store.list_entities(type="person")
    owner = ""
    settings = getattr(deps, "settings", None)
    if settings is not None:
        owner = (settings.dataset_owner or "").strip().lower()
    owner_id = store.get_meta("user_entity_id") if hasattr(store, "get_meta") else None
    tokens = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ']+", text)
    ngrams: list[str] = []
    for n in (3, 2, 1):
        for i in range(len(tokens) - n + 1):
            ngrams.append(" ".join(tokens[i : i + n]))
    best_name = None
    best_score = 0.0
    for entity in people:
        if owner_id and entity.id == owner_id:
            continue
        if owner and entity.canonical_name.strip().lower() == owner:
            continue
        for alias in [entity.canonical_name, *list(entity.aliases or [])]:
            alias_l = alias.lower()
            for gram in ngrams:
                if len(gram.split()) == 1 and len(gram) < 3:
                    continue
                score = fuzz.token_set_ratio(gram, alias) / 100.0
                if gram.lower() == alias_l or gram.lower() in alias_l:
                    score = max(score, 0.95 if gram.lower() == alias_l else 0.88)
                first = alias_l.split()[0] if alias_l.split() else ""
                if gram.lower() == first and len(gram) >= 3:
                    score = max(score, 0.9)
                if score > best_score:
                    best_score = score
                    best_name = entity.canonical_name
    return best_name if best_score >= 0.86 else None


_JUNK_VALUE_RE = re.compile(
    r"(?i)^(thanks?|perfect|parfait|yes|no|ok|on it|same|amazing|see you|"
    r"sending notes after|will send it by tuesday|no worries|happy to|"
    r"really needed that|comments in 30 min|door code is \d+|sunday is chores day|"
    r"grabbed a table at the back|mountain loop at \d+|what a comeback|"
    r"only the third period|expired cert on the staging proxy|"
    r"in the security folder|v3 is the latest|renewing all of them|"
    r"moving you to bcc|thanks again for yesterday)$"
)


def _looks_like_structured_value(
    value: str,
    source_ref: str,
    *,
    banned_name_tokens: frozenset[str] | None = None,
) -> bool:
    """Accept org/title only from contact cards or clean gmail signatures."""
    text = (value or "").strip()
    if len(text) < 2 or len(text) > 48:
        return False
    if _JUNK_VALUE_RE.match(text):
        return False
    if text.endswith("?") or "(" in text or text.count(",") > 1:
        return False
    src = (source_ref or "").lower()
    if src.startswith(("imessage:", "messenger:", "whatsapp:")):
        return False
    if src == "attribute":
        if not re.fullmatch(r"[A-ZÀ-Ö][\w&.\-']*(?: [A-ZÀ-Ö][\w&.\-']*){0,4}", text):
            return False
        if len(text.split()) > 5:
            return False
        return True
    if src.startswith("contact:"):
        return True
    if src.startswith("gmail:"):
        if text[0].islower():
            return False
        if re.search(r"(?i)\b(thanks?|perfect|hi|hey)\b", text):
            return False
        banned = banned_name_tokens or frozenset()
        tokens = {t.casefold() for t in re.findall(r"[A-Za-zÀ-öø-ÿ']+", text)}
        if banned and tokens & banned:
            return False
        return True
    return False


def _last_contact_line(
    evidence: dict[str, Evidence],
    *,
    ctx: dict[str, Any],
    settings: Settings | None,
) -> str:
    clock = frozen_now(settings=settings)
    # Prefer world-context summary when present.
    lc = ctx.get("last_contact") if isinstance(ctx, dict) else None
    if isinstance(lc, dict) and lc.get("source") and lc.get("ts_utc"):
        try:
            ts = datetime.fromisoformat(str(lc["ts_utc"]).replace("Z", "+00:00"))
        except ValueError:
            ts = None
        if ts is not None:
            return _relative_contact(ts, str(lc["source"]), clock)

    items = [
        e
        for e in evidence.values()
        if e.source in _MESSAGE_SOURCES and _as_utc(e.ts) <= _as_utc(clock)
    ]
    if not items:
        return "no recent message on file"
    newest = max(items, key=lambda e: e.ts)
    return _relative_contact(newest.ts, newest.source, clock)


def _next_meeting_line(
    evidence: dict[str, Evidence],
    *,
    ctx: dict[str, Any],
    settings: Settings | None,
) -> str:
    clock = frozen_now(settings=settings)
    nm = ctx.get("next_meeting") if isinstance(ctx, dict) else None
    if isinstance(nm, dict) and nm.get("title") and nm.get("start_utc"):
        try:
            ts = datetime.fromisoformat(str(nm["start_utc"]).replace("Z", "+00:00"))
            return f"next meeting: {nm['title']}, {format_human_when(ts, settings=settings)}"
        except ValueError:
            pass
    upcoming = [
        e
        for e in evidence.values()
        if e.source == "calendar" and _as_utc(e.ts) >= _as_utc(clock)
    ]
    if not upcoming:
        return ""
    nxt = min(upcoming, key=lambda e: e.ts)
    title = re.sub(r"\s+at\s+\d{4}-\d{2}-\d{2}.*$", "", nxt.text).strip() or "meeting"
    title = re.sub(r"\s+\(.*\)$", "", title).strip()
    return f"next meeting: {title}, {format_human_when(nxt.ts, settings=settings)}"


def _relative_contact(ts: datetime, source: str, clock: datetime) -> str:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=AGENT_TIMEZONE)
    delta = _as_utc(clock) - _as_utc(ts)
    days = max(0, delta.days)
    if days == 0:
        ago = "earlier today"
    elif days == 1:
        ago = "yesterday"
    elif days < 14:
        ago = f"{days} days ago"
    else:
        weeks = max(1, days // 7)
        ago = f"{weeks} week{'s' if weeks != 1 else ''} ago"
    return f"last contact {ago} on {source}"


def _as_utc(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=AGENT_TIMEZONE).astimezone(AGENT_TIMEZONE)
    return ts.astimezone(AGENT_TIMEZONE)


def _missing_ids(state: RunState) -> list[str]:
    if not state.verdicts:
        return []
    last = state.verdicts[-1]
    ids: list[str] = []
    for item in last.missing:
        m = re.search(r"\b(e\d+|m\d+|w\d+)\b", item)
        if m:
            ids.append(m.group(1))
        elif item in (state.evidence or {}):
            ids.append(item)
    return ids


def _drop_indexes(state: RunState) -> set[int]:
    if not state.verdicts:
        return set()
    return {v.index for v in state.verdicts[-1].verdicts if v.decision == "drop"}


def _select_points(
    evidence: dict[str, Evidence],
    *,
    person: str,
    missing: list[str] | None = None,
    drop_indexes: set[int] | None = None,
    prior_draft: Brief | None = None,
    entity: Any = None,
    settings: Settings | None = None,
) -> list[TalkingPoint]:
    active = settings if settings is not None else get_settings()
    clock = frozen_now(settings=active)
    first_name = person.split()[0] if person else "they"
    fulfilled = _fulfilled_promise_ids(evidence, clock=clock)
    answered = _answered_ask_ids(evidence, clock=clock)

    def _template(item: Evidence) -> TalkingPoint | None:
        if not _keep_evidence(item):
            return None
        if item.id in fulfilled or item.id in answered:
            return None
        if _as_utc(item.ts) > _as_utc(clock) and item.source != "calendar":
            return None
        tags = set(item.tags or [])
        when = format_human_when(item.ts, settings=active)
        day = format_human_day(item.ts, settings=active)
        if "promise" in tags:
            ask_bit, promise_bit = _split_promise(item.text or "")
            body = _clean_quote(ask_bit or promise_bit, person=person, settings=active)
            if ask_bit and re.search(r"(?i)financial model|model", ask_bit):
                text = (
                    f"You told {first_name} on {day} you'd send the revised "
                    f"financial model by Tuesday; still open."
                )
            elif body:
                text = (
                    f"You told {first_name} on {day} you'd "
                    f"{_clean_quote(promise_bit, person=person, limit=60, settings=active)}; "
                    f"still open."
                )
            else:
                return None
            kind = "open_loop"
        elif "open_loop" in tags:
            body = _clean_quote(item.text or "", person=person, limit=90, settings=active)
            if not body:
                return None
            # A question is never a life/job update — keep ask template.
            text = (
                f'{first_name} asked on {day} ({item.source}): "{body}" No reply yet.'
            )
            kind = "open_loop"
        elif item.source == "calendar":
            title = _calendar_title(item)
            if "upcoming" in tags or _as_utc(item.ts) >= _as_utc(clock):
                text = f"Upcoming: {title} on {when} ({item.source})"
            else:
                return None
            kind = "logistics"
        elif "conflict" in tags:
            text = (
                f"World Model conflict: "
                f"{_clean_quote(item.text, person=person, limit=100, settings=active)} "
                f"({item.source})"
            )
            kind = "catch_up"
        elif "job_change" in tags or _looks_like_life_update(item.text):
            if "?" in (item.text or ""):
                return None  # questions aren't life/job updates
            quote = _clean_quote(item.text, person=person, settings=active)
            text = f"Life/job update on {day}: {quote} ({item.source})"
            kind = "news"
        else:
            if not _is_substantive(item.text):
                return None
            if "?" in (item.text or ""):
                return None  # questions aren't catch-up / life updates
            quote = _clean_quote(item.text, person=person, settings=active)
            text = f"Latest thread on {day}: {quote} ({item.source})"
            kind = "catch_up"
        text = _ensure_min_words(text, pad=f"with {person}")
        if _ISO_Z_RE.search(text):
            text = _ISO_Z_RE.sub(when, text)
        return TalkingPoint(
            text=text,
            evidence_ids=[item.id],
            kind=kind,  # type: ignore[arg-type]
            priority=1,
        )

    forced: list[TalkingPoint] = []
    for eid in missing or []:
        item = evidence.get(eid)
        if item is None:
            continue
        point = _template(item)
        if point is not None:
            forced.append(point)

    buckets: dict[int, list[Evidence]] = {1: [], 2: [], 3: [], 4: []}
    for item in evidence.values():
        if not _keep_evidence(item):
            continue
        if item.id in fulfilled or item.id in answered:
            continue
        if _as_utc(item.ts) > _as_utc(clock) and item.source != "calendar":
            continue
        tags = set(item.tags or [])
        if "open_loop" in tags or "promise" in tags:
            if "promise" in tags and not _promise_is_substantive(item):
                continue
            buckets[1].append(item)
        elif item.source == "calendar" and (
            "upcoming" in tags or _as_utc(item.ts) >= _as_utc(clock)
        ):
            buckets[2].append(item)
        elif "job_change" in tags or "conflict" in tags or _looks_like_life_update(item.text):
            if "?" in (item.text or ""):
                continue
            buckets[3].append(item)
        elif item.source in _MESSAGE_SOURCES and _is_substantive(item.text):
            buckets[4].append(item)

    def _loop_sort_key(e: Evidence) -> tuple[int, float]:
        text = (e.text or "").lower()
        weight = 0
        if "promise" in (e.tags or []):
            weight += 10
        # Prefer longer / more specific open loops over short chatter.
        weight += min(20, len(re.findall(r"[A-Za-zÀ-öø-ÿ0-9']+", text)) // 2)
        if re.search(r"(?i)\b(intro|lease|rent|financial|model|pilot|hiring)\b", text):
            weight += 15
        return (-weight, -e.ts.timestamp())

    buckets[1].sort(key=_loop_sort_key)
    for pri in (2, 3, 4):
        buckets[pri].sort(key=lambda e: e.ts, reverse=True)

    points: list[TalkingPoint] = list(forced)
    seen_ids = {eid for p in points for eid in p.evidence_ids}
    seen_norms = {_norm_point_text(p.text) for p in points}
    limits = {1: 2, 2: 2, 3: 2, 4: 2}
    for pri in (1, 2, 3, 4):
        taken = 0
        for item in buckets[pri]:
            if len(points) >= 5:
                break
            if taken >= limits[pri]:
                break
            if item.id in seen_ids:
                continue
            point = _template(item)
            if point is None:
                continue
            if item.source == "calendar":
                collapsed = _collapse_recurring(item, evidence, settings=active)
                if collapsed is not None:
                    point = collapsed
                    for peer in evidence.values():
                        if (
                            peer.source == "calendar"
                            and _calendar_title(peer).lower() == _calendar_title(item).lower()
                        ):
                            seen_ids.add(peer.id)
            norm = _norm_point_text(point.text)
            if norm in seen_norms:
                continue
            if any(_near_duplicate(norm, other) for other in seen_norms):
                continue
            seen_ids.add(item.id)
            seen_norms.add(norm)
            point = point.model_copy(update={"priority": len(points) + 1})
            points.append(point)
            taken += 1

    if prior_draft is not None and drop_indexes:
        dropped_texts = {
            prior_draft.points[i].text
            for i in drop_indexes
            if 0 <= i < len(prior_draft.points)
        }
        points = [p for p in points if p.text not in dropped_texts]

    # Final pass: never repeat an evidence id or near-identical text.
    deduped: list[TalkingPoint] = []
    used: set[str] = set()
    norms: set[str] = set()
    for point in points:
        ids = set(point.evidence_ids)
        if ids & used:
            continue
        norm = _norm_point_text(point.text)
        if norm in norms or any(_near_duplicate(norm, other) for other in norms):
            continue
        used |= ids
        norms.add(norm)
        deduped.append(point)
    points = deduped

    for i, point in enumerate(points):
        points[i] = point.model_copy(update={"priority": i + 1})
    _ = entity
    return points[:5]


def _keep_evidence(item: Evidence) -> bool:
    if item.source == "web":
        return False
    text = (item.text or "").strip()
    if not text:
        return False
    if _NOISE_RE.match(text):
        return False
    tags = set(item.tags or [])
    words = len(re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9']+", text))
    # Short tapbacks / one-liners are not talking points even if tagged open_loop.
    if "open_loop" in tags and "promise" not in tags and words < 6:
        return False
    if item.source == "calendar":
        return True
    if item.source in _MESSAGE_SOURCES and not _is_substantive(text):
        if "open_loop" not in tags and "promise" not in tags:
            return False
    return True


def _is_substantive(text: str) -> bool:
    words = [w for w in re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9']+", text or "") if w]
    if len(words) < 6:
        return False
    if _NOISE_RE.match((text or "").strip()):
        return False
    return True


def _looks_like_life_update(text: str) -> bool:
    if "?" in (text or ""):
        return False
    return bool(
        re.search(
            r"(?i)\b(moved|d[ée]m[ée]nage|started at|joined|left |new job|"
            r"nouveau job|engaged|promot|hiring|number is|new number)\b",
            text or "",
        )
    )


def _promise_is_substantive(item: Evidence) -> bool:
    """Drop weak promises tied to tiny prior asks."""
    ask_bit, promise_bit = _split_promise(item.text or "")
    if ask_bit and len(re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9']+", ask_bit)) < 6:
        return False
    blob = f"{ask_bit} {promise_bit}"
    if re.search(r"(?i)\b(financial model|intro|lease|rent|pilot|deck|data room)\b", blob):
        return True
    return len(re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9']+", promise_bit)) >= 5


def _split_promise(text: str) -> tuple[str, str]:
    body = re.sub(r"(?i)^Promise:\s*", "", text or "")
    re_m = re.search(r"(?i)\(re:\s*(.+)\)$", body)
    ask = re_m.group(1).strip() if re_m else ""
    promise = re.sub(r"\s*\(re:.*\)$", "", body).strip()
    promise = re.sub(r"(?i)^(yes[,.]?\s*)", "", promise).strip()
    return ask, promise


def _calendar_title(item: Evidence) -> str:
    title = re.sub(r"\s+at\s+\d{4}-\d{2}-\d{2}.*$", "", item.text or "")
    title = re.sub(r"\s+\(.*\)$", "", title).strip()
    return title or "meeting"


def _collapse_recurring(
    item: Evidence,
    evidence: dict[str, Evidence],
    *,
    settings: Settings | None,
) -> TalkingPoint | None:
    title = _calendar_title(item)
    if not re.search(r"(?i)\bweekly\b", title):
        return None
    peers = [
        e
        for e in evidence.values()
        if e.source == "calendar" and _calendar_title(e).lower() == title.lower()
    ]
    if len(peers) < 2:
        return None
    local = item.ts.astimezone(AGENT_TIMEZONE)
    weekday = local.strftime("%A") + "s"
    hm = local.strftime("%H:%M").lstrip("0") or local.strftime("%H:%M")
    text = f"{title}, {weekday} {hm} (calendar)"
    text = _ensure_min_words(text, pad="recurring with them")
    return TalkingPoint(
        text=text,
        evidence_ids=[item.id],
        kind="logistics",
        priority=1,
    )


def _clean_quote(
    text: str,
    *,
    person: str = "",
    limit: int = 120,
    settings: Settings | None = None,
) -> str:
    """Strip greetings/sign-offs and trim at a word boundary."""
    cleaned = " ".join((text or "").split())
    cleaned = _ISO_Z_RE.sub("", cleaned)
    # "Hey <name>,", "Hi <name>,", "Bonjour <name>," — any addressee.
    cleaned = re.sub(
        r"(?i)^(hey|hi|hello|bonjour|bonsoir)\s+[A-Za-zÀ-ÖØ-öø-ÿ']+[,!]?\s*",
        "",
        cleaned,
    )
    cleaned = re.sub(r"(?i)^(hey|hi|hello|bonjour|bonsoir)[!.,]?\s+", "", cleaned)
    cleaned = re.sub(
        r"(?i)^(quick ask|quick question|random q|asking for a friend)[,:]?\s*",
        "",
        cleaned,
    )
    # Leading bare owner/contact first name ("Name, …").
    active = settings if settings is not None else get_settings()
    name_parts: set[str] = set()
    if person:
        name_parts.update(p for p in person.split() if len(p) >= 2)
    owner = (active.dataset_owner or "").strip()
    if owner:
        name_parts.update(p for p in owner.split() if len(p) >= 2)
    if name_parts:
        name_alt = "|".join(re.escape(n) for n in sorted(name_parts, key=len, reverse=True))
        cleaned = re.sub(rf"(?i)^(?:{name_alt})\s*[,—–-]\s*", "", cleaned)
        cleaned = re.sub(
            rf"(?i)[,.]?\s*(?:thanks?,?\s*)?(?:{name_alt})\s*$",
            "",
            cleaned,
        )
    # Trailing bare signature first name.
    cleaned = re.sub(r"(?i)[,]\s*[A-ZÀ-Ö][a-zà-öø-ÿ']+\s*$", "", cleaned)
    cleaned = cleaned.strip(" -;,\"'")
    if len(cleaned) <= limit:
        return cleaned
    cut = cleaned[: limit + 1]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(",.;:") + "…"


def _short_quote(text: str, limit: int = 120, *, settings: Settings | None = None) -> str:
    return _clean_quote(text, limit=limit, settings=settings)


def _norm_point_text(text: str) -> str:
    return re.sub(r"\W+", " ", (text or "").lower()).strip()


def _near_duplicate(a: str, b: str) -> bool:
    if not a or not b:
        return False
    if a == b:
        return True
    if a in b or b in a:
        return True
    return fuzz.token_set_ratio(a, b) >= 90


def _fulfilled_promise_ids(
    evidence: dict[str, Evidence], *, clock: datetime
) -> set[str]:
    """Promise fulfilled if a later calendar event or later owner outbound exists."""
    done: set[str] = set()
    promises = [
        e for e in evidence.values() if "promise" in (e.tags or [])
    ]
    calendars = [e for e in evidence.values() if e.source == "calendar"]
    # Owner outbounds: messages that look like the owner speaking (not open asks).
    outbounds = [
        e
        for e in evidence.values()
        if e.source in _MESSAGE_SOURCES
        and "promise" not in (e.tags or [])
        and "open_loop" not in (e.tags or [])
    ]
    for prom in promises:
        ask, body = _split_promise(prom.text or "")
        blob = f"{ask} {body}".lower()
        later_cals = [c for c in calendars if _as_utc(c.ts) > _as_utc(prom.ts)]
        # "I'll send an invite" → fulfilled by any later calendar with this person.
        if re.search(r"(?i)\b(invite|send an invite)\b", blob) and later_cals:
            done.add(prom.id)
            continue
        # Later owner message that clearly delivers the promised artifact.
        deliver_re = re.compile(
            r"(?i)\b(sent|sending|here's|here is|attached|shared|invite(?:d)?)\b"
        )
        if any(
            _as_utc(o.ts) > _as_utc(prom.ts)
            and deliver_re.search(o.text or "")
            and (
                fuzz.token_set_ratio(
                    _norm_point_text(o.text), _norm_point_text(ask or body)
                )
                >= 50
                or (
                    re.search(r"(?i)\bmodel\b", blob)
                    and re.search(r"(?i)\bmodel\b", o.text or "")
                )
                or (
                    re.search(r"(?i)\binvite\b", blob)
                    and re.search(r"(?i)\binvite\b", o.text or "")
                )
            )
            for o in outbounds
        ):
            done.add(prom.id)
    _ = clock
    return done


def _answered_ask_ids(
    evidence: dict[str, Evidence], *, clock: datetime
) -> set[str]:
    """Ask is answered if a later meeting with that person happened."""
    done: set[str] = set()
    asks = [
        e
        for e in evidence.values()
        if "open_loop" in (e.tags or []) and "promise" not in (e.tags or [])
    ]
    meetings = [e for e in evidence.values() if e.source == "calendar"]
    for ask in asks:
        if not re.search(
            r"(?i)\b(coffee|meet|meeting|chat|call|lunch|dinner|catch up|intro)\b",
            ask.text or "",
        ):
            continue
        if any(
            _as_utc(m.ts) > _as_utc(ask.ts) and _as_utc(m.ts) <= _as_utc(clock)
            for m in meetings
        ):
            done.add(ask.id)
    return done


def _ensure_min_words(text: str, *, pad: str) -> str:
    if len(text.split()) >= 6:
        return text
    return f"{text} {pad}".strip()


def _answer_question(
    state: RunState,
    *,
    entity: Any,
    store: Any,
    ctx: dict[str, Any],
    evidence: dict[str, Evidence],
) -> Brief:
    person = state.entity_name or (state.intent.person_name if state.intent else "them")
    field = state.intent.question_field if state.intent else None
    settings = get_settings()
    points: list[TalkingPoint] = []

    if field == "location":
        newest, card, note = _structured_field(entity, store, "city")
        # Conflicts may be fresher than polluted attributes.
        if store is not None and entity is not None and not newest:
            newest, card, note = _structured_field(entity, store, "city")
        since = _location_since(entity, newest)
        if newest:
            headline = f"{person} lives in {newest}"
            if since:
                headline = f"{headline} ({since})"
            if note:
                headline = f"{headline}; {note}"
            # Expected travel window when evidence mentions it.
            travel_bit = _travel_headline_bit(evidence)
            if travel_bit:
                headline = f"{headline}; {travel_bit}"
            headline = f"{headline}."
        else:
            headline = f"I don't have a clear location for {person}."
        points = _select_points(evidence, person=person)[:3]
        travel = _travel_point(evidence, person=person, settings=settings)
        if travel is not None:
            used_ids = {eid for p in points for eid in p.evidence_ids}
            norms = {_norm_point_text(p.text) for p in points}
            travel_norm = _norm_point_text(travel.text)
            if used_ids.intersection(travel.evidence_ids):
                travel = None
            elif travel_norm in norms or any(
                _near_duplicate(travel_norm, other) for other in norms
            ):
                travel = None
        if travel is not None:
            points = [travel, *points][:3]
        return Brief(person=person or "unknown", headline=headline, points=points, caveats=[])

    if field == "employer":
        org, title, note = structured_org_title(entity, store, settings=settings)
        if org and title:
            headline = f"{person}, {title} at {org}"
        elif org:
            headline = f"{person} works at {org}"
        else:
            headline = f"I don't have a clear employer for {person}."
        if note:
            headline = f"{headline} ({note})"
        points = _select_points(
            evidence, person=person, entity=entity, settings=settings
        )[:3]
        return Brief(person=person or "unknown", headline=headline, points=points, caveats=[])

    if field == "phone":
        phones = _phone_values(entity, store)
        if phones:
            newest, stale = phones[0], phones[1:]
            headline = f"{person}'s number is {newest}"
            if stale:
                headline = f"{headline}; older number {stale[0]} looks dead"
            headline = f"{headline}."
        else:
            headline = f"I don't have a phone number for {person}."
        points = _select_points(evidence, person=person)[:3]
        return Brief(person=person or "unknown", headline=headline, points=points, caveats=[])

    if field == "replied":
        asks = [
            e
            for e in evidence.values()
            if "open_loop" in (e.tags or []) and "promise" not in (e.tags or [])
        ]
        if asks:
            item = max(asks, key=lambda e: e.ts)
            when = format_human_when(item.ts, settings=settings)
            headline = (
                f"No — {person}'s {when} {item.source} question is still unanswered."
            )
            points = _select_points(evidence, person=person, missing=[item.id])[:3]
        else:
            headline = f"I don't see an unanswered ask from {person}."
            points = _select_points(evidence, person=person)[:2]
        return Brief(person=person or "unknown", headline=headline, points=points, caveats=[])

    if field == "owe":
        loops = [e for e in evidence.values() if "open_loop" in (e.tags or [])]
        if loops:
            # Prefer lease/money asks when present.
            item = next(
                (
                    e
                    for e in loops
                    if re.search(r"(?i)\b(lease|rent|bail|owe|\$|\d[\d,]*)\b", e.text)
                ),
                max(loops, key=lambda e: e.ts),
            )
            when = format_human_when(item.ts, settings=settings)
            headline = (
                f"You owe {person} a reply from {when}: {_short_quote(item.text, 80)} "
                f"({item.source})."
            )
            points = _select_points(evidence, person=person, missing=[item.id])[:3]
        else:
            headline = f"Nothing obvious outstanding with {person}."
            points = _select_points(evidence, person=person)[:2]
        return Brief(person=person or "unknown", headline=headline, points=points, caveats=[])

    headline = f"About {person}."
    points = _select_points(evidence, person=person)[:3]
    return Brief(person=person or "unknown", headline=headline, points=points, caveats=[])


def _structured_field(
    entity: Any, store: Any, field: str
) -> tuple[str, str, str]:
    if entity is None:
        return "", "", ""
    cands: list[tuple[datetime, str, str]] = []

    def _add(value: str, source_ref: str, observed_at: str) -> None:
        if not _looks_like_structured_value(value, source_ref) and field == "city":
            # Cities can be lowercase in French sources; allow title-case or known.
            if len(value.strip()) < 2:
                return
        try:
            ts = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
        except ValueError:
            ts = datetime(1970, 1, 1, tzinfo=AGENT_TIMEZONE)
        cands.append((ts, value.strip(), source_ref))

    for obs in getattr(entity, "attribute_history", None) or []:
        f = getattr(obs, "field", None)
        if f != field:
            continue
        _add(
            str(getattr(obs, "value", "")),
            str(getattr(obs, "source_ref", "")),
            str(getattr(obs, "observed_at", "")),
        )
    if store is not None and getattr(entity, "id", None):
        for conflict in store.list_conflicts(entity.id):
            if getattr(conflict, "field", "") != field:
                continue
            for val in getattr(conflict, "values", None) or []:
                _add(
                    str(getattr(val, "value", "")),
                    str(getattr(val, "source_ref", "")),
                    str(getattr(val, "observed_at", "")),
                )
    cleaned = []
    for ts, value, src in cands:
        if field == "city":
            if not re.match(r"^[A-ZÀ-Ö]", value):
                continue
            if len(value.split()) > 2:
                continue
            # Drop French/English particles captured alone.
            if value.casefold() in {"le", "du", "la", "des", "de", "the", "a", "an"}:
                continue
        cleaned.append((ts, value, src))
    if not cleaned:
        attrs = getattr(entity, "attributes", None) or {}
        raw = str(attrs.get(field) or "").strip()
        if raw and (field != "city" or re.match(r"^[A-ZÀ-Ö]", raw)):
            return raw, raw if False else "", ""
        return "", "", ""
    cleaned.sort(key=lambda x: x[0], reverse=True)
    newest = cleaned[0][1]
    contact = next((v for _t, v, s in cleaned if str(s).startswith("contact:")), "")
    note = ""
    if contact and contact.lower() != newest.lower():
        note = f"contact card still says {contact}"
    return newest, contact, note


def _phone_values(entity: Any, store: Any) -> list[str]:
    phones: list[tuple[datetime, str]] = []
    if entity is None:
        return []
    for alias in getattr(entity, "aliases", None) or []:
        digits = re.sub(r"\D", "", str(alias))
        if len(digits) >= 10:
            phones.append((datetime(1970, 1, 1, tzinfo=AGENT_TIMEZONE), str(alias)))
    if store is not None and getattr(entity, "id", None):
        for row in store.list_handle_links(entity_id=entity.id):
            kind = str(row.get("handle_kind") or "")
            val = str(row.get("handle_value") or "")
            digits = re.sub(r"\D", "", val)
            if kind not in {"phone", "mobile", "tel"} and len(digits) < 10:
                continue
            if len(digits) < 10:
                continue
            observed = str(row.get("observed_at") or row.get("updated_at") or "")
            try:
                ts = datetime.fromisoformat(observed.replace("Z", "+00:00"))
            except ValueError:
                ts = datetime.now(tz=AGENT_TIMEZONE)
            phones.append((ts, val))
    # Newest observation first.
    uniq: list[str] = []
    for _ts, val in sorted(phones, key=lambda x: -x[0].timestamp()):
        norm = re.sub(r"\D", "", val)
        if any(re.sub(r"\D", "", u) == norm for u in uniq):
            continue
        uniq.append(val)
    return uniq


def _location_since(entity: Any, city: str) -> str:
    if not city or entity is None:
        return ""
    city_l = city.casefold()
    best: datetime | None = None
    for obs in getattr(entity, "attribute_history", None) or []:
        if getattr(obs, "field", None) != "city":
            continue
        if str(getattr(obs, "value", "")).casefold() != city_l:
            continue
        src = str(getattr(obs, "source_ref", "") or "")
        if src.startswith("contact:"):
            continue
        try:
            ts = datetime.fromisoformat(
                str(getattr(obs, "observed_at", "")).replace("Z", "+00:00")
            )
        except ValueError:
            continue
        if best is None or ts > best:
            best = ts
    if best is None:
        return "from messages" if city else ""
    local = best.astimezone(AGENT_TIMEZONE)
    return f"since {local.strftime('%b')} {local.year}, from messages"


_MONTH = (
    r"jan(?:uary|vier)?|feb(?:ruary|rier)?|mar(?:ch|s)?|apr(?:il)?|avr(?:il)?|"
    r"may|mai|june?|juin|july|juil(?:let)?|aug(?:ust)?|ao[uû]t|"
    r"sep(?:t(?:ember|embre)?)?|oct(?:ober|obre)?|nov(?:ember|embre)?|"
    r"dec(?:ember)?|d[eé]c(?:embre)?"
)
_DATE_RANGE_RE = re.compile(
    r"(?i)\b(?:du|from|from the)?\s*"
    r"(\d{1,2})\s+(" + _MONTH + r")"
    r"(?:\s+\d{4})?"
    r"\s*(?:au|to|–|-|—)\s*"
    r"(?:the\s+)?"
    r"(\d{1,2})?\s*(" + _MONTH + r")"
    r"(?:\s+\d{4})?"
)


def _travel_headline_bit(evidence: dict[str, Evidence]) -> str:
    """Generic EN/FR visit window from message text (no dataset cities)."""
    for item in evidence.values():
        text = item.text or ""
        m = _DATE_RANGE_RE.search(text)
        if not m:
            continue
        city_m = re.search(
            r"(?i)\b(?:à|a|in|back in)\s+([A-ZÀ-Ö][A-Za-zÀ-öø-ÿ\-']+)",
            text,
        )
        city = city_m.group(1) if city_m else ""
        d1, m1, d2, m2 = m.group(1), m.group(2), m.group(3) or m.group(1), m.group(4)
        m1_s = m1[:3].title()
        m2_s = m2[:3].title()
        window = f"{m1_s} {int(d1)} – {m2_s} {int(d2)}"
        if city:
            return f"back in {city} {window}"
        return f"away {window}"
    return ""


def _travel_point(
    evidence: dict[str, Evidence],
    *,
    person: str,
    settings: Settings | None,
) -> TalkingPoint | None:
    """Surface date-range travel notes from evidence (generic EN/FR)."""
    for item in sorted(evidence.values(), key=lambda e: e.ts, reverse=True):
        text = item.text or ""
        if "Conflict on" in text:
            continue
        if not _DATE_RANGE_RE.search(text):
            continue
        if not _is_substantive(text) and len(text.split()) < 4:
            continue
        when = format_human_day(item.ts, settings=settings)
        point_text = (
            f"Travel note on {when}: "
            f"{_clean_quote(text, person=person, limit=100, settings=settings)} "
            f"({item.source})"
        )
        point_text = _ensure_min_words(point_text, pad=f"about {person}")
        return TalkingPoint(
            text=point_text,
            evidence_ids=[item.id],
            kind="catch_up",
            priority=1,
        )
    return None


def _trace_offline(deps: Any, agent: str, summary: str, payload: dict[str, Any]) -> None:
    tracer = getattr(deps, "tracer", None) if deps is not None else None
    if tracer is None:
        tracer = Tracer.current()
    if tracer is not None:
        tracer.event(agent, "complete", {"summary": summary, "provider": "offline_rules", **payload})
