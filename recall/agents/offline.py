"""Deterministic offline fallbacks when the sample LLM cache misses.

Used on ``SampleCacheMiss`` (or when there is no NVIDIA key). Every fallback
traces ``provider = "offline_rules"``.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

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
_LABEL_HINTS: list[tuple[re.Pattern[str], list[str]]] = [
    (re.compile(r"(?i)\bmy\s+landlord\b|\bproprio\b"), ["landlord", "proprio", "kassem"]),
    (re.compile(r"(?i)\bmy\s+mom\b|\bmaman\b"), ["maman", "mom", "nadia"]),
    (re.compile(r"(?i)\bmy\s+dad\b|\bpapa\b"), ["papa", "dad"]),
    (re.compile(r"(?i)\bfrom\s+my\s+mba\b|\bmba\b"), ["mba", "benali"]),
    (re.compile(r"(?i)\bfrom\s+uoft\b|\buoft\b"), ["uoft", "utoronto"]),
]


def route_intent(
    text: str,
    *,
    deps: Any = None,
    settings: Settings | None = None,
) -> Intent:
    """Rule-based router: kind, person_name, when."""
    active = settings if settings is not None else get_settings()
    clock = frozen_now(settings=active)
    utterance = (text or "").strip()
    kind = _detect_kind(utterance)
    when = parse_when_phrase(utterance, clock)
    person = _extract_person(utterance, deps=deps)
    confidence = 0.9 if person or kind in {"note", "calendar", "open_loops", "suggest"} else 0.7
    if kind == "unknown":
        confidence = 0.3
    intent = Intent(
        kind=kind if kind != "unknown" else ("brief" if person else "unknown"),
        person_name=person,
        when=when,
        action_request=None,
        meal_type=None,
        sources_plan=list(DEFAULT_SOURCES_PLAN),
        confidence=confidence,
    )
    _trace_offline(
        deps,
        "router",
        f"kind={intent.kind} person={intent.person_name!r} confidence={intent.confidence:.2f}",
        {"intent": intent.model_dump(mode="json")},
    )
    return intent


def write_brief(state: RunState, deps: Any = None) -> Brief:
    """Build a Brief only from the evidence pack (no invented facts)."""
    person = state.entity_name or (state.intent.person_name if state.intent else "unknown")
    evidence = state.evidence or {}

    if not evidence:
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

    entity = None
    store = getattr(deps, "world_store", None) if deps is not None else None
    if store is not None and state.entity_id:
        entity = store.get_entity(state.entity_id)
    org, title, org_note = _org_title(entity, evidence)
    last_contact = _last_contact_line(evidence, settings=getattr(deps, "settings", None))
    role_bit = ""
    if title and org:
        role_bit = f", {title} at {org}"
    elif org:
        role_bit = f" at {org}"
    elif title:
        role_bit = f", {title}"
    headline = f"{person}{role_bit}; {last_contact}"
    if org_note:
        headline = f"{headline} ({org_note})"

    points = _select_points(evidence, person=person)
    caveats: list[str] = []
    if len(evidence) < 3 or len(points) <= 1:
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
    """Rule-based critic: drop bad citations, flag missing open loops / job changes."""
    if not state.drafts:
        raise ValueError("critic requires a draft")
    draft = state.drafts[-1]
    pack = state.evidence or {}
    entity_name = (state.entity_name or "").strip().lower()

    verdicts: list[PointVerdict] = []
    kept_points: list[TalkingPoint] = []
    for index, point in enumerate(draft.points):
        valid_ids = [eid for eid in point.evidence_ids if eid in pack]
        if not valid_ids:
            verdicts.append(
                PointVerdict(
                    index=index,
                    decision="drop",
                    reason="Evidence ids missing from pack.",
                    fix_hint="Cite ids that exist in EVIDENCE.",
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
        missing.append(f"Uncited open loop [{open_loop_ids[0]}]")
        overall = "revise"
    conflict_ids = [eid for eid, item in pack.items() if "conflict" in (item.tags or [])]
    if conflict_ids and not any(eid in cited for eid in conflict_ids):
        missing.append(f"Uncited conflict [{conflict_ids[0]}]")
        overall = "revise"
    job_ids = [eid for eid, item in pack.items() if "job_change" in (item.tags or [])]
    if job_ids and not any(eid in cited for eid in job_ids):
        missing.append(f"Uncited job change [{job_ids[0]}]")
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


def parse_when_phrase(text: str, clock: datetime) -> str | None:
    """Return a normalized when string (ISO date or weekday label) relative to clock."""
    local = clock.astimezone(AGENT_TIMEZONE)
    match = _RELATIVE_WHEN.search(text or "")
    if not match:
        return None
    raw = match.group(1).strip().lower()
    if raw == "today" or raw == "tonight":
        return local.date().isoformat()
    if raw == "tomorrow":
        return (local.date() + timedelta(days=1)).isoformat()
    for name, idx in _WEEKDAYS.items():
        if name in raw:
            delta = (idx - local.weekday()) % 7
            if delta == 0:
                delta = 7  # "on Wednesday" from Wednesday → next week unless same day meeting
            # Prefer upcoming including today when phrase is just the weekday
            if name == raw or raw == name:
                delta = (idx - local.weekday()) % 7
            target = local.date() + timedelta(days=delta)
            return target.isoformat()
    # "on November 6" / "the 21st"
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
        year = local.year
        try:
            return datetime(year, month, day, tzinfo=AGENT_TIMEZONE).date().isoformat()
        except ValueError:
            return raw
    day_m = re.search(r"(?i)the\s+(\d{1,2})(?:st|nd|rd|th)", text or "")
    if day_m:
        day = int(day_m.group(1))
        try:
            return local.replace(day=day).date().isoformat()
        except ValueError:
            return raw
    if raw.startswith("at "):
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


def _detect_kind(text: str) -> str:
    if _NOTE_RE.search(text):
        return "note"
    if _SUGGEST_RE.search(text):
        return "suggest"
    if _CALENDAR_RE.search(text):
        return "calendar"
    if _OPEN_LOOPS_RE.search(text):
        return "open_loops"
    if _MEET_RE.search(text) or re.search(r"(?i)\b(brief|catch me up|seeing)\b", text):
        return "brief"
    return "unknown"


def _extract_person(text: str, *, deps: Any = None) -> str | None:
    lowered = text or ""
    # Relational labels first (landlord / mom / MBA).
    for pattern, needles in _LABEL_HINTS:
        if pattern.search(lowered):
            hit = _fuzzy_world_match(needles, deps=deps, min_score=70)
            if hit:
                return hit

    meet = _MEET_RE.search(lowered)
    fragment = ""
    if meet:
        fragment = meet.group(1).strip(" .,!?")
        # Strip trailing relative time words left in the capture.
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

    # Multi-token fragment (e.g. "Omar Haddad", "Rachel Goldberg") wins as-is.
    if fragment and len(fragment.split()) >= 2:
        if fragment.lower() not in {"someone", "them", "him", "her"}:
            return fragment

    # Short name / nickname: expand via world only when uniquely matched.
    if fragment and len(fragment.split()) == 1:
        unique = _unique_world_name(fragment, deps=deps)
        if unique:
            return unique
        return fragment

    world_hit = _fuzzy_world_match_utterance(lowered, deps=deps)
    if world_hit:
        return world_hit
    return None


def _unique_world_name(needle: str, *, deps: Any = None) -> str | None:
    """Return canonical name when exactly one person matches ``needle``."""
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
    if len(uniq) == 1:
        return uniq[0]
    return None


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
        aliases = [entity.canonical_name, *list(entity.aliases or [])]
        # Also match title/org-like tokens embedded in the canonical name (proprio, MBA).
        for alias in aliases:
            alias_l = alias.lower()
            for gram in ngrams:
                gram_tokens = gram.split()
                if len(gram_tokens) == 1 and len(gram) < 3:
                    continue
                score = fuzz.token_set_ratio(gram, alias) / 100.0
                if gram.lower() == alias_l or gram.lower() in alias_l:
                    score = max(score, 0.95 if gram.lower() == alias_l else 0.88)
                # Nickname / short first name
                first = alias_l.split()[0] if alias_l.split() else ""
                if gram.lower() == first and len(gram) >= 3:
                    score = max(score, 0.9)
                if score > best_score:
                    best_score = score
                    best_name = entity.canonical_name
    if best_score >= 0.86 and best_name:
        return best_name
    return None


def _fuzzy_world_match(
    needles: list[str], *, deps: Any = None, min_score: int = 75
) -> str | None:
    store = getattr(deps, "world_store", None) if deps is not None else None
    if store is None:
        return None
    people = store.list_entities(type="person")
    best_name = None
    best = 0.0
    for entity in people:
        pool = " ".join([entity.canonical_name, *list(entity.aliases or [])]).lower()
        for needle in needles:
            if needle.lower() in pool:
                return entity.canonical_name
            score = fuzz.partial_ratio(needle.lower(), pool) / 100.0
            if score > best:
                best = score
                best_name = entity.canonical_name
    if best * 100 >= min_score:
        return best_name
    return None


def _looks_like_org_or_title(value: str) -> bool:
    text = (value or "").strip()
    if len(text) < 2 or len(text) > 48:
        return False
    lowered = text.lower()
    if lowered in {
        "yes",
        "no",
        "ok",
        "the",
        "a",
        "at",
        "back",
        "sending notes after",
        "thanks",
    }:
        return False
    if " " in text and not any(c.isupper() for c in text):
        return False
    # Chatty fragments.
    if text.endswith("?") or text.count(",") > 2:
        return False
    return True


def _org_title(entity: Any, evidence: dict[str, Evidence]) -> tuple[str, str, str]:
    """Org/title from entity attributes when clean, else evidence; note conflicts."""
    org = ""
    title = ""
    note = ""
    attrs = getattr(entity, "attributes", None) or {}
    attr_org = str(attrs.get("org") or "").strip()
    attr_title = str(attrs.get("title") or "").strip()
    if _looks_like_org_or_title(attr_org):
        org = attr_org
    if _looks_like_org_or_title(attr_title):
        title = attr_title

    ev_orgs: list[str] = []
    for item in sorted(evidence.values(), key=lambda e: e.ts, reverse=True):
        if item.source == "calendar":
            continue
        text = item.text
        m = re.search(
            r"(?i)\b(CEO|CTO|Partner|Principal|Founder|Manager|Director|Landlord)\b"
            r"(?:\s+(?:at|of|,)\s+)([A-Z][\w&.\- ]{1,40})",
            text,
        )
        if m:
            if not title and _looks_like_org_or_title(m.group(1)):
                title = m.group(1).strip()
            cand = m.group(2).strip(" .,")
            if _looks_like_org_or_title(cand):
                ev_orgs.append(cand)
        # Signature-style "Name\nTitle, Org"
        m2 = re.search(
            r"(?i)\b(Payloop|Northwind Capital|Maple Ridge Ventures|Stellarfin|"
            r"Fieldnote|Tidepool|Hydrolab|Brightloop)\b",
            text,
        )
        if m2:
            ev_orgs.append(m2.group(1))

    uniq: list[str] = []
    for o in ev_orgs:
        if o.lower() not in {u.lower() for u in uniq}:
            uniq.append(o)
    if uniq:
        if not org:
            org = uniq[0]
        elif org.lower() not in {u.lower() for u in uniq}:
            note = f"contact card says {org}; recent mail says {uniq[0]}"
            org = uniq[0]
    return org, title, note


def _last_contact_line(evidence: dict[str, Evidence], *, settings: Settings | None) -> str:
    clock = frozen_now(settings=settings)
    items = [e for e in evidence.values() if e.source != "web"]
    if not items:
        return "no recent contact on file"
    newest = max(items, key=lambda e: e.ts)
    ts = newest.ts
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=AGENT_TIMEZONE)
    delta = clock.astimezone(ts.tzinfo) - ts.astimezone(ts.tzinfo)
    days = max(0, delta.days)
    if days == 0:
        ago = "earlier today"
    elif days == 1:
        ago = "yesterday"
    elif days < 14:
        ago = f"{days} days ago"
    else:
        weeks = days // 7
        ago = f"{weeks} week{'s' if weeks != 1 else ''} ago"
    return f"last contact {ago} on {newest.source}"


def _select_points(evidence: dict[str, Evidence], *, person: str) -> list[TalkingPoint]:
    ranked: list[tuple[int, Evidence]] = []
    for item in evidence.values():
        tags = set(item.tags or [])
        if "open_loop" in tags:
            pri = 1
        elif item.source == "calendar":
            pri = 2
        elif "job_change" in tags:
            pri = 3
        elif "conflict" in tags:
            # Only keep clean org/city conflicts — skip chatty Conflict lines.
            if not re.search(r"(?i)Conflict on (org|city|title):", item.text or ""):
                continue
            values = re.findall(r"Conflict on \w+: (.+) vs (.+?)(?:\s*$)", item.text or "")
            if values:
                left, right = values[0][0], values[0][1]
                if not (_looks_like_org_or_title(left.split("(")[0].strip()) and _looks_like_org_or_title(right.split("(")[0].strip())):
                    continue
            pri = 7
        elif item.source == "memory":
            pri = 5
        elif item.source == "web":
            pri = 6
        else:
            pri = 4
        ranked.append((pri, item))
    ranked.sort(key=lambda pair: (pair[0], -pair[1].ts.timestamp()))

    points: list[TalkingPoint] = []
    seen_text: set[str] = set()
    for pri, item in ranked:
        if len(points) >= 5:
            break
        text = " ".join(item.text.split())
        if len(text) > 160:
            text = text[:157].rstrip() + "…"
        key = text.lower()[:80]
        if key in seen_text:
            continue
        seen_text.add(key)
        kind: str = "catch_up"
        if "open_loop" in (item.tags or []):
            kind = "open_loop"
        elif item.source == "calendar":
            kind = "logistics"
        elif item.source == "memory":
            kind = "memory"
        elif item.source == "web":
            kind = "news"
        elif "job_change" in (item.tags or []):
            kind = "news"
        points.append(
            TalkingPoint(
                text=text,
                evidence_ids=[item.id],
                kind=kind,  # type: ignore[arg-type]
                priority=len(points) + 1,
            )
        )
    if not points:
        # Sparse: one honest line.
        first = next(iter(evidence.values()))
        points.append(
            TalkingPoint(
                text=f"Only thin notes on {person}: {first.text[:120]}",
                evidence_ids=[first.id],
                kind="catch_up",
                priority=1,
            )
        )
    return points


def _trace_offline(deps: Any, agent: str, summary: str, payload: dict[str, Any]) -> None:
    tracer = getattr(deps, "tracer", None) if deps is not None else None
    if tracer is None:
        tracer = Tracer.current()
    if tracer is not None:
        tracer.event(agent, "complete", {"summary": summary, "provider": "offline_rules", **payload})
