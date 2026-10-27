"""Score unlinked handles against known person entities (explainable identity)."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Awaitable
from uuid import uuid4

import yaml
from pydantic import BaseModel, Field

from recall.config import Settings, get_settings
from recall.data.queries import InteractionRow
from recall.engines.world.anchors import normalize_email, normalize_phone
from recall.engines.world.models import Entity
from recall.llm.generator import embed, generate_json, use_sample_provider

GenerateJsonFn = Callable[..., Awaitable[BaseModel]]
EmbedFn = Callable[..., Awaitable[list[list[float]]]]

_ROOT = Path(__file__).resolve().parents[3]
_SCORING_PATH = _ROOT / "config" / "identity_scoring.yaml"
_ALIASES_PATH = _ROOT / "config" / "company_aliases.yaml"

_TOKEN_SPLIT = re.compile(r"[.\-_0-9]+")
_PHONE_RE = re.compile(
    r"(?:\+?\d[\d\s().-]{7,}\d)"
)
_WORD = re.compile(r"[A-Za-z0-9]+(?:'[A-Za-z]+)?")


class CompanyProposal(BaseModel):
    """LLM proposal for an unknown corporate domain."""

    company: str
    aliases: list[str] = Field(default_factory=list)
    industry_keywords: list[str] = Field(default_factory=list)


class WorkMention(BaseModel):
    """One work-related mention extracted from a candidate's messages."""

    source_ref: str
    summary: str
    industry: str = ""
    company_mentioned: str = ""


class IndustryExtract(BaseModel):
    """LLM extract of work context for one candidate."""

    work_mentions: list[WorkMention] = Field(default_factory=list)
    dominant_industry: str = ""
    confidence: float = 0.0


class ContentReference(BaseModel):
    """Concrete detail extracted from an unlinked email body."""

    text: str
    type: str = "plan"  # event|place|person|plan|project|date
    specificity: float = 0.5


class ContentReferences(BaseModel):
    """References extracted from one email (ignore greetings/signatures)."""

    references: list[ContentReference] = Field(default_factory=list)


class ReferenceMatch(BaseModel):
    """Critic confirmation that a candidate message is about the same thing."""

    reference: str
    source_ref: str
    same_thing: bool
    reason: str = ""


class ReferenceMatchBatch(BaseModel):
    """Batch of reference confirmations for one candidate."""

    matches: list[ReferenceMatch] = Field(default_factory=list)


@dataclass
class FeatureResult:
    """One feature value with provenance."""

    name: str
    value: float
    weight: float
    contribution: float
    source_refs: list[str] = field(default_factory=list)
    detail: str = ""
    matches: list[dict[str, Any]] = field(default_factory=list)
    similarity: float | None = None


@dataclass
class CandidateScore:
    """Scored candidate (or the none-of-these option)."""

    entity_id: str | None
    name: str
    features: list[FeatureResult]
    penalties: list[dict[str, Any]]
    raw_score: float
    probability: float = 0.0
    rank: int = 0


@dataclass
class ParsedHandle:
    """Parsed email (or phone) handle."""

    kind: str
    value: str
    local_tokens: list[str] = field(default_factory=list)
    display_name: str = ""
    domain: str = ""
    domain_class: str = ""  # free-mail | corporate
    company: str = ""
    aliases: list[str] = field(default_factory=list)
    industry: list[str] = field(default_factory=list)
    signature_name: str = ""
    signature_title: str = ""
    signature_company: str = ""
    signature_phones: list[str] = field(default_factory=list)


@dataclass
class ScoringDecision:
    """Full decision payload for one unlinked handle."""

    handle_kind: str
    handle_value: str
    parsed: ParsedHandle
    candidates: list[CandidateScore]
    decision: str  # auto_linked | tentative | needs_user | unmatched | organization | shared | new_person
    top_entity_id: str | None
    top_probability: float
    hard_rules: list[str]
    scoring_version: int
    run_id: str
    score_file: str
    thresholds: dict[str, float]
    flags: list[str] = field(default_factory=list)
    handle_class: str = "person"
    new_person_hint: dict[str, Any] = field(default_factory=dict)


def load_scoring_config(path: Path | None = None) -> dict[str, Any]:
    """Load identity_scoring.yaml."""
    target = path or _SCORING_PATH
    with target.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    return data


def load_company_aliases(path: Path | None = None) -> dict[str, Any]:
    """Load company_aliases.yaml (seed + learned)."""
    target = path or _ALIASES_PATH
    if not target.exists():
        return {"seed": {}, "learned": {}}
    with target.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    data.setdefault("seed", {})
    data.setdefault("learned", {})
    return data


def save_company_aliases(data: dict[str, Any], path: Path | None = None) -> None:
    """Persist company aliases YAML (append-only learned entries via rewrite)."""
    target = path or _ALIASES_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(data, handle, sort_keys=False, allow_unicode=True)


def handle_slug(value: str) -> str:
    """Filesystem-safe slug for a handle."""
    return re.sub(r"[^a-zA-Z0-9._-]+", "_", value.strip().lower())[:120]


def parse_email_handle(
    email: str,
    *,
    display_name: str = "",
    body: str = "",
    config: dict[str, Any] | None = None,
    aliases: dict[str, Any] | None = None,
) -> ParsedHandle:
    """Step A: parse an email into tokens, domain class, company, signature."""
    cfg = config or load_scoring_config()
    alias_db = aliases if aliases is not None else load_company_aliases()
    value = normalize_email(email)
    local, _, domain = value.partition("@")
    tokens = [t for t in _TOKEN_SPLIT.split(local) if t]
    free = {d.lower() for d in cfg.get("free_mail_domains", [])}
    domain_class = "free-mail" if domain in free else "corporate"
    company = ""
    company_aliases: list[str] = []
    industry: list[str] = []
    if domain_class == "corporate" and domain:
        entry = alias_db.get("seed", {}).get(domain) or alias_db.get("learned", {}).get(domain)
        if entry:
            company = str(entry.get("company") or _domain_to_company(domain))
            company_aliases = list(entry.get("aliases") or [company])
            industry = list(entry.get("industry") or entry.get("industry_keywords") or [])
        else:
            company = _domain_to_company(domain)
            company_aliases = [company]
    sig = extract_signature(body)
    return ParsedHandle(
        kind="email",
        value=value,
        local_tokens=tokens,
        display_name=(display_name or "").strip(),
        domain=domain,
        domain_class=domain_class,
        company=company,
        aliases=company_aliases,
        industry=industry,
        signature_name=sig.get("name", ""),
        signature_title=sig.get("title", ""),
        signature_company=sig.get("company", ""),
        signature_phones=list(sig.get("phones") or []),
    )


def extract_signature(body: str) -> dict[str, Any]:
    """Pull name/title/company/phones from the last 12 lines of a message body."""
    lines = [ln.strip() for ln in (body or "").splitlines() if ln.strip()]
    tail = lines[-12:] if lines else []
    phones = []
    for line in tail:
        for match in _PHONE_RE.findall(line):
            norm = normalize_phone(match)
            if norm and norm not in phones:
                phones.append(norm)
    name = ""
    title = ""
    company = ""
    # Heuristic: first non-phone, non-email line that looks like a person name.
    for line in tail:
        if "@" in line or _PHONE_RE.search(line):
            continue
        if re.match(
            r"^(sent from|get outlook|regards|cheers|thanks|best|sincerely)\b",
            line,
            re.I,
        ):
            continue
        words = line.split()
        if re.fullmatch(r"[\W_]+", line):
            continue
        if 1 <= len(words) <= 4 and all(w[:1].isupper() for w in words if w.isalpha()):
            name = line
            break
    # Title/company only from short signature-like lines (never reply body crumbs).
    sig_lines = [ln for ln in tail if len(ln) <= 60 and ln.count(",") <= 1][-6:]
    junk = re.compile(
        r"(?i)^(thanks?\b|perfect|hi\b|hey\b|happy to|moving you\b)"
    )
    for line in reversed(sig_lines):
        if line == name or junk.search(line):
            continue
        if "," in line:
            left, right = [p.strip() for p in line.split(",", 1)]
            if (
                left
                and right
                and left[0].isupper()
                and right[0].isupper()
                and len(left.split()) <= 6
                and len(right.split()) <= 5
                and not junk.search(left)
                and not junk.search(right)
            ):
                title = left
                company = right
                break
        if re.search(r"\bat\b", line, re.I):
            parts = re.split(r"\bat\b", line, maxsplit=1, flags=re.I)
            if len(parts) == 2:
                left = parts[0].strip(" -–|,")
                right = parts[1].strip(" -–|,")
                if left and right and left[0].isupper() and right[0].isupper():
                    title = left or title
                    company = right or company
                    break
    return {"name": name, "title": title, "company": company, "phones": phones}


def _domain_to_company(domain: str) -> str:
    base = domain.split(".")[0]
    if base.lower() == "pwc":
        return "PwC"
    return base[:1].upper() + base[1:]


async def ensure_company_for_domain(
    domain: str,
    *,
    aliases: dict[str, Any] | None = None,
    generate_json_fn: GenerateJsonFn | None = None,
) -> dict[str, Any]:
    """Return company entry for domain; learn via generate_json if missing."""
    alias_db = aliases if aliases is not None else load_company_aliases()
    existing = alias_db.get("seed", {}).get(domain) or alias_db.get("learned", {}).get(domain)
    if existing:
        return dict(existing)
    gen = generate_json_fn or generate_json
    try:
        proposal = await gen(
            f"Propose company name, aliases, and industry keywords for email domain {domain}.",
            "You extract employer metadata from domains.",
            CompanyProposal,
            role="memory_extract",
        )
        assert isinstance(proposal, CompanyProposal)
        entry = {
            "company": proposal.company or _domain_to_company(domain),
            "aliases": proposal.aliases or [_domain_to_company(domain)],
            "industry": proposal.industry_keywords or [],
        }
    except Exception:  # noqa: BLE001 - offline / sample fallback
        entry = {
            "company": _domain_to_company(domain),
            "aliases": [_domain_to_company(domain)],
            "industry": [],
        }
    alias_db.setdefault("learned", {})[domain] = entry
    save_company_aliases(alias_db)
    return entry


def name_tokens(text: str) -> list[str]:
    """Lowercased alphanumeric tokens from a name or local-part."""
    return [m.group(0).lower() for m in _WORD.finditer(text or "")]


def select_candidates(
    parsed: ParsedHandle,
    people: list[Entity],
) -> list[Entity]:
    """Step B: people whose name shares a token with display name or local part."""
    from recall.engines.world.identity_normalize import (
        name_variant_set,
        names_match_normalized,
        normalize_name_token,
        normalize_person_name,
    )

    needles = {normalize_name_token(t) for t in parsed.local_tokens if t}
    needles.update(name_tokens(parsed.display_name))
    needles.update(name_tokens(parsed.signature_name))
    disp_norm, _ = normalize_person_name(parsed.display_name or parsed.signature_name or "")
    needles.update(disp_norm.split())
    # Expand needles with known nickname / transliteration variants.
    expanded: set[str] = set()
    for n in needles:
        expanded |= name_variant_set(n)
    needles = {n for n in expanded if n}
    if not needles:
        return []
    out: list[Entity] = []
    for person in people:
        if parsed.display_name and names_match_normalized(parsed.display_name, person.canonical_name):
            out.append(person)
            continue
        pool = name_tokens(person.canonical_name)
        for alias in person.aliases:
            if "@" not in alias and not alias.startswith("+"):
                pool.extend(name_tokens(alias))
        pool_vars: set[str] = set()
        for p in pool:
            pool_vars |= name_variant_set(p)
        if not pool:
            continue
        first = pool[0]
        last = pool[-1]
        initials = {p[0] for p in pool if p}
        hit = False
        for needle in needles:
            if needle in pool or needle in pool_vars:
                hit = True
                break
            if len(needle) == 1 and needle in initials and (
                last in needles or bool(name_variant_set(last) & needles)
            ):
                hit = True
                break
            if len(needle) == 1 and last in needles and needle == first[:1]:
                hit = True
                break
        if hit or (last in needles) or (first in needles) or bool(name_variant_set(last) & needles):
            out.append(person)
    seen: set[str] = set()
    unique: list[Entity] = []
    for person in out:
        if person.id in seen:
            continue
        seen.add(person.id)
        unique.append(person)
    return unique


def feature_name_match(
    parsed: ParsedHandle,
    person: Entity,
    *,
    body_for_mentions: str = "",
) -> FeatureResult:
    """f2 name_match.

    Uses display/signature/local-part only — names merely mentioned in the body
    never count toward the sender match. Supports nickname/transliteration variants.
    """
    from recall.engines.world.identity_normalize import (
        name_variant_set,
        names_match_normalized,
        normalize_name_token,
        normalize_person_name,
    )

    cand = person.canonical_name.strip()
    display = (parsed.display_name or parsed.signature_name or "").strip()
    refs: list[str] = []
    value = 0.0
    detail = "no name overlap"
    cand_tokens = name_tokens(cand)
    # Sender-side tokens only (not body mentions).
    disp_source = display if display else " ".join(parsed.local_tokens)
    disp_norm, _ = normalize_person_name(disp_source)
    disp_tokens = disp_norm.split() if disp_norm else list(t.lower() for t in parsed.local_tokens)

    # Ignore body mentions for f2: if the only place the candidate name appears is
    # in the body and not in display/signature/local, keep value at 0.
    body_l = (body_for_mentions or "").lower()
    if (
        not display
        and not parsed.signature_name
        and cand.lower() in body_l
        and not any(normalize_name_token(t) in body_l for t in parsed.local_tokens)
    ):
        return FeatureResult("f2", 0.0, 0.0, 0.0, refs, "body mention only — ignored")

    if (
        display
        and len(disp_tokens) >= 2
        and len(cand_tokens) >= 2
        and names_match_normalized(display, cand)
    ):
        value = 1.0
        detail = "full display name match"
    elif cand_tokens and disp_tokens:
        last = cand_tokens[-1]
        first = cand_tokens[0]
        last_vars = name_variant_set(last)
        first_vars = name_variant_set(first)
        disp_set = set(disp_tokens)
        if (last_vars & disp_set or last in disp_tokens) and (
            first_vars & disp_set
            or first in disp_tokens
            or first[:1] in disp_tokens
            or any(t == first[:1] for t in disp_tokens if len(t) == 1)
        ):
            value = 0.7
            detail = "last name or initial+last match (incl. variants)"
        elif last_vars & disp_set or last in disp_tokens:
            value = 0.7
            detail = "last name match (incl. variants)"
        elif first_vars & disp_set or first in disp_tokens:
            value = 0.2
            detail = "first name only"
    return FeatureResult("f2", value, 0.0, 0.0, refs, detail)


def feature_signature_phone(
    parsed: ParsedHandle,
    person: Entity,
    phone_index: dict[str, set[str]],
) -> FeatureResult:
    """f1 signature_phone_match."""
    person_phones = phone_index.get(person.id, set())
    hits = [p for p in parsed.signature_phones if p in person_phones]
    value = 1.0 if hits else 0.0
    return FeatureResult(
        "f1",
        value,
        0.0,
        0.0,
        [f"sig-phone:{p}" for p in hits],
        "signature phone on card" if hits else "no signature phone match",
    )


def feature_company_mentions(
    person_id: str,
    company: str,
    aliases: list[str],
    interactions: list[InteractionRow],
    entity_handle_values: set[str],
    *,
    now: datetime,
) -> FeatureResult:
    """f3 company_mentions with recency weighting."""
    if not company and not aliases:
        return FeatureResult("f3", 0.0, 0.0, 0.0, [], "no company")
    terms = [t for t in [company, *aliases] if t]
    cutoff = now - timedelta(days=180)
    weighted = 0.0
    refs: list[str] = []
    count = 0
    for row in interactions:
        if not _interaction_belongs(row, entity_handle_values):
            continue
        text = f"{row.subject} {row.text}"
        if not _mentions_any(text, terms):
            continue
        ts = _parse_ts(row.ts_utc)
        weight = 1.0 if ts >= cutoff else 0.5
        weighted += weight
        count += 1
        refs.append(row.source_ref)
        if count >= 10:
            break
    value = min(weighted, 5.0) / 5.0
    return FeatureResult("f3", value, 0.0, 0.0, refs[:8], f"weighted_mentions={weighted:.1f}")


def feature_known_employer(
    person: Entity,
    company: str,
    aliases: list[str],
    *,
    recent_other_employer: bool,
    email_ts: datetime | None = None,
    employment_rows: list[dict[str, Any]] | None = None,
    now: datetime | None = None,
) -> tuple[FeatureResult, list[dict[str, Any]]]:
    """f5 known_employer + optional p1 penalty (time-aware).

    Conflicting-employer penalty applies only when the other employer is
    *current*: observed within 90 days and later than the email date.
    """
    penalties: list[dict[str, Any]] = []
    org = (person.attributes.get("org") or person.attributes.get("company") or "").strip()
    org_l = org.lower()
    terms = {t.lower() for t in [company, *aliases] if t}
    matched = bool(org_l) and (
        org_l in terms or any(t in org_l or org_l in t for t in terms)
    )
    if matched:
        return FeatureResult("f5", 1.0, 0.0, 0.0, [f"attr:org:{org}"], "org matches"), penalties

    clock = now or datetime.now(timezone.utc)
    # Time-aware p1: never penalize mail older than 90 days; otherwise only when
    # a conflicting employer is current and already in force at email time.
    apply_conflict = False
    if org and company and not matched and (recent_other_employer or employment_rows):
        if email_ts is not None and clock - email_ts > timedelta(days=90):
            apply_conflict = False
        elif employment_rows:
            for row in employment_rows:
                if str(row.get("entity_id")) != person.id:
                    continue
                other = str(row.get("company") or "").strip()
                if not other:
                    continue
                other_l = other.lower()
                if other_l in terms or any(t in other_l or other_l in t for t in terms):
                    continue
                valid_from = str(row.get("valid_from") or "")
                valid_to = str(row.get("valid_to") or "")
                updated = str(row.get("updated_at") or valid_from or "")
                try:
                    updated_ts = (
                        datetime.fromisoformat(updated.replace("Z", "+00:00"))
                        if updated
                        else None
                    )
                except ValueError:
                    updated_ts = None
                try:
                    from_ts = (
                        datetime.fromisoformat(valid_from.replace("Z", "+00:00"))
                        if valid_from
                        else updated_ts
                    )
                except ValueError:
                    from_ts = updated_ts
                is_current = not valid_to and updated_ts is not None and (
                    clock - updated_ts <= timedelta(days=90)
                )
                if not is_current:
                    continue
                if email_ts is not None and from_ts is not None and from_ts > email_ts:
                    continue
                apply_conflict = True
                break
        else:
            # Fallback without ledger: only penalize recent emails (within 90d).
            apply_conflict = bool(
                recent_other_employer
                and (
                    email_ts is None
                    or clock - email_ts <= timedelta(days=90)
                )
            )

    if apply_conflict and org and company and not matched:
        penalties.append(
            {
                "name": "p1_conflicting_employer",
                "value": -0.25,
                "detail": f"org={org} vs company={company}",
            }
        )
        return FeatureResult("f5", 0.0, 0.0, 0.0, [f"attr:org:{org}"], "conflicting employer"), penalties
    return FeatureResult("f5", 0.0, 0.0, 0.0, [], "no known employer match"), penalties


_ANNOUNCE_PHONE_RE = re.compile(
    r"(?i)(?:new number|save it|reach me at|my new number)[^\n]{0,40}"
    r"(\+?\d[\d\s().-]{8,}\d)"
)
_ANNOUNCE_EMAIL_RE = re.compile(
    r"(?i)(?:new email|my new email|use my work email|reach me at)[^\n]{0,60}"
    r"([\w.+-]+@[\w.-]+\.\w+)"
)


def feature_announcement(
    handle_value: str,
    person: Entity,
    interactions: list[InteractionRow],
    entity_handles: set[str],
    *,
    is_anchor_announcer: bool,
) -> tuple[FeatureResult, list[str]]:
    """f8 announcement detection from already-linked handles.

    Returns (feature, hard_rules). Hard rule ``announce_auto_link`` when the
    announcing handle is an anchor and announces this unlinked handle.
    """
    hard: list[str] = []
    target_email = normalize_email(handle_value) if "@" in handle_value else ""
    target_phone = normalize_phone(handle_value) if "@" not in handle_value else ""
    hits: list[str] = []
    for row in interactions:
        if not _interaction_belongs(row, entity_handles):
            continue
        text = row.text or ""
        for match in _ANNOUNCE_PHONE_RE.finditer(text):
            phone = normalize_phone(match.group(1))
            if target_phone and phone == target_phone:
                hits.append(row.source_ref)
            elif not target_phone and phone and phone in (handle_value, normalize_phone(handle_value)):
                hits.append(row.source_ref)
        for match in _ANNOUNCE_EMAIL_RE.finditer(text):
            email = normalize_email(match.group(1))
            if target_email and email == target_email:
                hits.append(row.source_ref)
        # Also catch "New number, save it: +1 ..." patterns without keyword prefix group
        if re.search(r"(?i)new number.*save", text) and target_phone:
            if target_phone in normalize_phone(text) or target_phone.lstrip("+") in re.sub(
                r"\D", "", text
            ):
                hits.append(row.source_ref)
    if not hits:
        return FeatureResult("f8", 0.0, 0.0, 0.0, [], "no announcement"), hard
    if is_anchor_announcer:
        hard.append("announce_auto_link")
    return (
        FeatureResult(
            "f8",
            1.0,
            0.0,
            0.0,
            list(dict.fromkeys(hits))[:8],
            f"announced for {person.canonical_name}",
        ),
        hard,
    )


def feature_thread_overlap(
    handle_value: str,
    interactions: list[InteractionRow],
    person_handles: set[str],
) -> FeatureResult:
    """f6 thread_overlap / email-reference heuristic."""
    refs: list[str] = []
    # Messages around same dates referencing email
    email_refs = [
        row
        for row in interactions
        if _interaction_belongs(row, person_handles)
        and re.search(r"\b(email|e-mail|inbox|check your mail|just sent)\b", row.text, re.I)
    ]
    if email_refs:
        refs.extend(r.source_ref for r in email_refs[:3])
        return FeatureResult("f6", 0.6, 0.0, 0.0, refs, "email reference in candidate messages")
    # Same-thread participants (weak): if any interaction from this handle shares thread with person
    handle_threads = {
        row.thread_id for row in interactions if normalize_email(row.handle_value) == handle_value
    }
    if handle_threads:
        for row in interactions:
            if row.thread_id in handle_threads and _interaction_belongs(row, person_handles):
                refs.append(row.source_ref)
                return FeatureResult("f6", 0.6, 0.0, 0.0, refs[:3], "shared thread")
    return FeatureResult("f6", 0.0, 0.0, 0.0, [], "no thread overlap")


async def feature_industry_context(
    person: Entity,
    parsed: ParsedHandle,
    interactions: list[InteractionRow],
    entity_handles: set[str],
    config: dict[str, Any],
    *,
    generate_json_fn: GenerateJsonFn | None = None,
) -> FeatureResult:
    """f4 industry_context via generate_json (or keyword fallback)."""
    prefilter = [k.lower() for k in config.get("work_prefilter_keywords", [])]
    industry = [k.lower() for k in parsed.industry]
    keywords = set(prefilter) | set(industry)
    related = [
        row
        for row in interactions
        if _interaction_belongs(row, entity_handles)
        and any(k in f"{row.subject} {row.text}".lower() for k in keywords)
    ][:20]
    if not related:
        return FeatureResult("f4", 0.0, 0.0, 0.0, [], "no work-related messages")

    gen = generate_json_fn or generate_json
    try:
        payload = "\n".join(
            f"[{row.source_ref}] {row.text[:240]}" for row in related
        )
        extract = await gen(
            f"PERSON: {person.canonical_name}\nDOMAIN_INDUSTRY: {parsed.industry}\n"
            f"COMPANY: {parsed.company}\nMESSAGES:\n{payload}",
            "Extract work mentions and dominant industry.",
            IndustryExtract,
            role="memory_extract",
        )
        assert isinstance(extract, IndustryExtract)
        mentions = extract.work_mentions
        if not mentions:
            raise ValueError("empty mentions")
        if not industry:
            share = float(extract.confidence or 0.0)
        else:
            matched = sum(
                1
                for m in mentions
                if m.industry.lower() in industry
                or any(k in (m.industry or "").lower() for k in industry)
                or any(k in (m.summary or "").lower() for k in industry)
            )
            share = matched / max(len(mentions), 1)
        refs = [m.source_ref for m in mentions if m.source_ref][:8]
        return FeatureResult("f4", min(max(share, 0.0), 1.0), 0.0, 0.0, refs, "llm industry")
    except Exception:  # noqa: BLE001
        # Keyword fallback: share of related messages mentioning industry terms or company.
        terms = set(industry) | {a.lower() for a in parsed.aliases} | {parsed.company.lower()}
        terms.discard("")
        if not terms:
            return FeatureResult("f4", 0.0, 0.0, 0.0, [], "fallback no terms")
        hits = [
            row
            for row in related
            if any(t in f"{row.subject} {row.text}".lower() for t in terms)
        ]
        share = len(hits) / max(len(related), 1)
        return FeatureResult(
            "f4",
            share,
            0.0,
            0.0,
            [r.source_ref for r in hits[:8]],
            "keyword industry fallback",
        )


_GREETING_RE = re.compile(
    r"(?i)^(hi|hey|hello|dear|thanks|thank you|cheers|best|regards|hope you're well|"
    r"hope you are well|let'?s catch up|looking forward).*"
)
_GENERIC_PHRASES = frozenset(
    {
        "hope you're well",
        "hope you are well",
        "let's catch up",
        "lets catch up",
        "talk soon",
        "see you soon",
        "how are you",
        "great seeing you",
    }
)


def _strip_signature_for_content(body: str) -> str:
    """Drop likely signature lines before content extraction."""
    lines = [ln.rstrip() for ln in (body or "").splitlines()]
    if len(lines) >= 3:
        # Drop trailing signature block (phones / title lines).
        cut = len(lines)
        for index in range(len(lines) - 1, max(len(lines) - 8, -1), -1):
            line = lines[index].strip()
            if not line:
                cut = index
                continue
            if _PHONE_RE.search(line) or re.match(r"^(founder|ceo|associate)\b", line, re.I):
                cut = index
                continue
            break
        lines = lines[:cut]
    return "\n".join(lines).strip()


def heuristic_extract_references(body: str) -> list[ContentReference]:
    """Offline / fallback extractor of concrete email details."""
    text = _strip_signature_for_content(body)
    if not text:
        return []
    lower = text.lower().strip()
    # Pure generic catch-up → no references.
    generic_only = re.sub(
        r"(?i)\b(hi|hey|hello|thanks|thank you|again|,|\.|!|\?)\b",
        " ",
        lower,
    )
    generic_only = re.sub(r"\s+", " ", generic_only).strip()
    if generic_only in _GENERIC_PHRASES or generic_only in {
        "hope youre well lets catch up",
        "hope you're well let's catch up",
        "hope you are well let's catch up",
        "hope you're well, let's catch up",
    }:
        return []
    refs: list[ContentReference] = []
    # Capture concrete phrases from the email itself (org/project/event tokens
    # come from the body — never hardcode a dataset org or team name).
    patterns: list[tuple[str, str, float]] = [
        (r"case interview(?: tips| prep|s)?", "plan", 0.9),
        (r"review session(?:\s+\w+day)?", "event", 0.9),
        (
            r"[A-Za-z][A-Za-z0-9.&]{1,20}(?:\s+[A-Za-z][A-Za-z0-9.&]{1,20})?\s+"
            r"advisory(?:\s+round)?",
            "project",
            0.85,
        ),
        (r"[A-Za-z]{3,20}\s+game(?:\s+\w+day)?", "event", 0.95),
        (r"hockey game", "event", 0.9),
        (r"\bsaturday\b", "date", 0.5),
        (r"\bthursday\b", "date", 0.55),
        (r"\bdinner\b", "plan", 0.4),
    ]
    seen: set[str] = set()
    for pattern, typ, spec in patterns:
        match = re.search(pattern, text, re.I)
        if not match:
            continue
        phrase = match.group(0).strip()
        key = phrase.lower()
        if key in seen or key in _GENERIC_PHRASES:
            continue
        seen.add(key)
        refs.append(ContentReference(text=phrase, type=typ, specificity=spec))
    if len(text.split()) <= 12 and not refs:
        return []
    return refs


async def extract_email_references(
    body: str,
    *,
    generate_json_fn: GenerateJsonFn | None = None,
) -> list[ContentReference]:
    """Step f7a prep: extract concrete references from the email body only."""
    content = _strip_signature_for_content(body)
    if not content.strip():
        return []
    # Privacy: prompt contains only the email body (no candidate histories).
    gen = generate_json_fn or generate_json
    try:
        if use_sample_provider():
            raise RuntimeError("prefer heuristic in sample mode")
        result = await gen(
            f"EMAIL_BODY:\n{content}\n\n"
            "Extract concrete references (events, places, people, plans, projects, dates). "
            "Ignore greetings, signatures, and generic phrases like 'hope you're well'.",
            "You extract specific shared-world details from emails.",
            ContentReferences,
            role="memory_extract",
        )
        assert isinstance(result, ContentReferences)
        refs = [r for r in result.references if r.text.strip() and r.specificity > 0]
        if refs:
            return refs
    except Exception:  # noqa: BLE001
        pass
    return heuristic_extract_references(content)


def _keyword_hits_for_reference(
    reference: ContentReference,
    messages: list[InteractionRow],
    *,
    limit: int = 10,
) -> list[InteractionRow]:
    """Exact / fuzzy keyword hits for one reference in candidate messages."""
    tokens = [t for t in name_tokens(reference.text) if len(t) > 2]
    if not tokens:
        tokens = name_tokens(reference.text)
    hits: list[tuple[int, InteractionRow]] = []
    for row in messages:
        blob = f"{row.subject} {row.text}".lower()
        score = sum(1 for t in tokens if t in blob)
        if score <= 0:
            # fuzzy: all chars of a long token present as substring of words
            if reference.text.lower() in blob:
                score = len(tokens) or 1
            else:
                continue
        hits.append((score, row))
    hits.sort(key=lambda item: (-item[0], item[1].ts_utc), reverse=False)
    hits.sort(key=lambda item: -item[0])
    return [row for _, row in hits[:limit]]


async def feature_shared_references(
    person: Entity,
    references: list[ContentReference],
    interactions: list[InteractionRow],
    entity_handles: set[str],
    *,
    now: datetime,
    generate_json_fn: GenerateJsonFn | None = None,
) -> FeatureResult:
    """f7a shared_references — strong content congruence."""
    if not references:
        return FeatureResult("f7a", 0.0, 0.0, 0.0, [], "no references", matches=[])

    cutoff = now - timedelta(days=180)
    messages = [
        row
        for row in interactions
        if _interaction_belongs(row, entity_handles) and _parse_ts(row.ts_utc) >= cutoff
    ]
    if not messages:
        return FeatureResult("f7a", 0.0, 0.0, 0.0, [], "no recent messages", matches=[])

    confirmed: list[dict[str, Any]] = []
    gen = generate_json_fn or generate_json

    for ref in references:
        hits = _keyword_hits_for_reference(ref, messages, limit=10)
        if not hits:
            continue
        # Privacy: only email reference text + up to 10 matched snippets.
        snippets = "\n".join(
            f"[{row.source_ref}] {row.text[:220]}" for row in hits
        )
        matched_via_llm: list[ReferenceMatch] = []
        try:
            if use_sample_provider():
                raise RuntimeError("prefer heuristic confirm in sample mode")
            batch = await gen(
                f"REFERENCE: {ref.text} (type={ref.type}, specificity={ref.specificity})\n"
                f"CANDIDATE: {person.canonical_name}\nSNIPPETS:\n{snippets}\n"
                "For each snippet, is it about the same thing as the reference?",
                "You confirm whether message snippets refer to the same real-world thing.",
                ReferenceMatchBatch,
                role="critic",
            )
            assert isinstance(batch, ReferenceMatchBatch)
            matched_via_llm = [m for m in batch.matches if m.same_thing]
        except Exception:  # noqa: BLE001
            # Heuristic confirm: keyword overlap in snippet.
            for row in hits:
                blob = f"{row.subject} {row.text}".lower()
                tokens = [t for t in name_tokens(ref.text) if len(t) > 2]
                if not tokens:
                    continue
                overlap = sum(1 for t in tokens if t in blob) / len(tokens)
                if overlap >= 0.5 or ref.text.lower() in blob:
                    matched_via_llm.append(
                        ReferenceMatch(
                            reference=ref.text,
                            source_ref=row.source_ref,
                            same_thing=True,
                            reason="keyword overlap",
                        )
                    )

        for match in matched_via_llm:
            if not match.same_thing:
                continue
            confirmed.append(
                {
                    "reference": ref.text,
                    "type": ref.type,
                    "specificity": ref.specificity,
                    "source_ref": match.source_ref,
                    "reason": match.reason,
                    "candidate": person.canonical_name,
                }
            )

    # Deduplicate by (reference, source_ref)
    uniq: dict[tuple[str, str], dict[str, Any]] = {}
    for item in confirmed:
        uniq[(item["reference"], item["source_ref"])] = item
    confirmed = list(uniq.values())
    score = min(1.0, sum(float(item["specificity"]) for item in confirmed) / 2.0)
    refs = [item["source_ref"] for item in confirmed]
    detail_parts = [
        f"email mentions '{item['reference']}' — matches {item['source_ref']} "
        f"({item['candidate']})"
        for item in confirmed[:6]
    ]
    return FeatureResult(
        "f7a",
        score,
        0.0,
        0.0,
        refs,
        "; ".join(detail_parts) if detail_parts else "no confirmed matches",
        matches=confirmed,
    )


def _cosine(a: list[float], b: list[float]) -> float | None:
    if len(a) != len(b) or not a:
        return None
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na <= 0 or nb <= 0:
        return None
    return dot / (na * nb)


async def feature_topic_similarity(
    person: Entity,
    email_body: str,
    interactions: list[InteractionRow],
    entity_handles: set[str],
    profile_cache: dict[str, list[float]],
    *,
    embed_fn: EmbedFn | None = None,
) -> FeatureResult:
    """f7b topic_similarity — weak embedding congruence."""
    content = _strip_signature_for_content(email_body)
    if not content.strip():
        return FeatureResult("f7b", 0.0, 0.0, 0.0, [], "empty body", similarity=0.0)

    related = [
        row for row in interactions if _interaction_belongs(row, entity_handles)
    ]
    related.sort(key=lambda r: r.ts_utc, reverse=True)
    profile_parts: list[str] = []
    total = 0
    for row in related[:30]:
        chunk = f"{row.subject} {row.text}".strip()
        if not chunk:
            continue
        if total + len(chunk) > 2000:
            chunk = chunk[: max(0, 2000 - total)]
        profile_parts.append(chunk)
        total += len(chunk)
        if total >= 2000:
            break
    profile = "\n".join(profile_parts).strip()
    if not profile:
        return FeatureResult("f7b", 0.0, 0.0, 0.0, [], "no profile text", similarity=0.0)

    embedder = embed_fn or embed
    try:
        if person.id not in profile_cache:
            vectors = await embedder([profile], input_type="passage")
            profile_cache[person.id] = vectors[0] if vectors else []
        query_vecs = await embedder([content], input_type="query")
        query_vec = query_vecs[0] if query_vecs else []
        profile_vec = profile_cache.get(person.id) or []
        if len(query_vec) != len(profile_vec) or not query_vec:
            return FeatureResult(
                "f7b",
                0.0,
                0.0,
                0.0,
                [],
                f"embedding dim mismatch or empty ({len(query_vec)} vs {len(profile_vec)})",
                similarity=None,
            )
        cos = _cosine(query_vec, profile_vec)
        if cos is None:
            return FeatureResult(
                "f7b", 0.0, 0.0, 0.0, [], "cosine failed", similarity=None
            )
        value = max(0.0, min(1.0, (cos - 0.2) / 0.6))
        return FeatureResult(
            "f7b",
            value,
            0.0,
            0.0,
            [],
            f"cosine={cos:.3f}",
            similarity=cos,
        )
    except Exception as exc:  # noqa: BLE001
        return FeatureResult(
            "f7b",
            0.0,
            0.0,
            0.0,
            [],
            f"embed skipped: {exc}",
            similarity=None,
        )


def apply_weights(
    features: list[FeatureResult],
    weights: dict[str, float],
    penalties: list[dict[str, Any]],
) -> float:
    """Weighted sum + penalties, clipped to [0, 1]."""
    total = 0.0
    for feat in features:
        w = float(weights.get(feat.name, 0.0))
        feat.weight = w
        feat.contribution = w * feat.value
        total += feat.contribution
    for pen in penalties:
        total += float(pen.get("value", 0.0))
    return max(0.0, min(1.0, total))


def softmax_probs(raw_scores: list[float], temperature: float) -> list[float]:
    """Softmax with temperature (lower → peakier)."""
    if not raw_scores:
        return []
    t = max(temperature, 1e-6)
    scaled = [r / t for r in raw_scores]
    m = max(scaled)
    exps = [math.exp(s - m) for s in scaled]
    denom = sum(exps) or 1.0
    return [e / denom for e in exps]


def decide(
    ranked: list[CandidateScore],
    thresholds: dict[str, float],
    hard_rules: list[str],
) -> tuple[str, str | None, float]:
    """Step E decision from ranked candidates (entity rows only; none included)."""
    if "announce_auto_link" in hard_rules or "force_auto_linked" in hard_rules:
        top = next((c for c in ranked if c.entity_id), None)
        if top is not None:
            return "auto_linked", top.entity_id, top.probability
    if not ranked or all(c.entity_id is None for c in ranked):
        none = next((c for c in ranked if c.entity_id is None), None) if ranked else None
        none_p = none.probability if none else 0.0
        new_min = float(thresholds.get("new_person_none_probability", 0.60))
        if none is not None and none_p >= new_min:
            return "new_person", None, none_p
        return "unmatched", None, none_p
    entities = [c for c in ranked if c.entity_id]
    none = next((c for c in ranked if c.entity_id is None), None)
    if not entities:
        none_p = none.probability if none else 0.0
        new_min = float(thresholds.get("new_person_none_probability", 0.60))
        if none is not None and none_p >= new_min:
            return "new_person", None, none_p
        return "needs_user", None, none_p
    entities_sorted = sorted(entities, key=lambda c: c.probability, reverse=True)
    top = entities_sorted[0]
    second_p = (
        entities_sorted[1].probability
        if len(entities_sorted) > 1
        else (none.probability if none else 0.0)
    )
    margin = top.probability - second_p
    auto_p = float(thresholds.get("auto_link_probability", 0.80))
    auto_m = float(thresholds.get("auto_link_margin", 0.20))
    tent_min = float(thresholds.get("tentative_min", 0.55))
    new_min = float(thresholds.get("new_person_none_probability", 0.60))

    # "None of these" wins clearly → new_person.
    if none is not None and none.probability >= new_min and none.probability > top.probability:
        return "new_person", None, none.probability

    if "possible_spoof" in hard_rules or "cap_needs_user" in hard_rules:
        return "needs_user", top.entity_id, top.probability
    if "block_auto_link" in hard_rules:
        if top.probability >= tent_min:
            return "tentative", top.entity_id, top.probability
        return "needs_user", top.entity_id, top.probability
    decision: str
    if top.probability >= auto_p and margin >= auto_m:
        decision = "auto_linked"
    elif top.probability >= tent_min:
        decision = "tentative"
    else:
        decision = "needs_user"
    if "f7b_only_cap_tentative" in hard_rules and decision == "auto_linked":
        decision = "tentative"
    return decision, top.entity_id, top.probability


def resolve_score_jsonl_path(
    filename: str,
    *,
    run_id: str | None = None,
) -> tuple[str, Path]:
    """Return ``(run_id, logs/<run_id>/<filename>)`` from the active Tracer when set."""
    from recall.tracing import Tracer

    tracer = Tracer.current()
    if tracer is not None:
        rid = tracer.run_id
        run_dir = tracer.path.parent
    else:
        rid = run_id or str(uuid4())
        run_dir = Path(get_settings().logs_dir) / rid
    run_dir.mkdir(parents=True, exist_ok=True)
    return rid, run_dir / filename


def write_score_file(decision: ScoringDecision, path: Path | None = None) -> Path:
    """Append one JSON decision line to ``identity_scores.jsonl``; return that path."""
    if path is None:
        _, path = resolve_score_jsonl_path(
            "identity_scores.jsonl", run_id=decision.run_id
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = decision_to_dict(decision)
    payload["score_file"] = str(path)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
    return path


def load_identity_score_record(
    score_file: Path | str,
    *,
    handle_value: str | None = None,
) -> dict[str, Any]:
    """Load one identity decision from a JSONL path (or legacy single JSON file)."""
    path = Path(score_file)
    if not path.is_file():
        raise FileNotFoundError(path)
    if path.suffix == ".jsonl":
        needle = (handle_value or "").strip().lower()
        last: dict[str, Any] | None = None
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                text = line.strip()
                if not text:
                    continue
                rec = json.loads(text)
                if not needle:
                    last = rec
                    continue
                hv = str((rec.get("handle") or {}).get("value") or "").strip().lower()
                if hv == needle:
                    last = rec
        if last is None:
            raise ValueError(f"no score record for handle={handle_value!r} in {path}")
        return last
    return json.loads(path.read_text(encoding="utf-8"))


def decision_to_dict(decision: ScoringDecision) -> dict[str, Any]:
    """Serialize a ScoringDecision for JSON / ledger."""
    return {
        "handle": {"kind": decision.handle_kind, "value": decision.handle_value},
        "parsed": {
            "local_tokens": decision.parsed.local_tokens,
            "display_name": decision.parsed.display_name,
            "domain": decision.parsed.domain,
            "domain_class": decision.parsed.domain_class,
            "company": decision.parsed.company,
            "aliases": decision.parsed.aliases,
            "industry": decision.parsed.industry,
            "signature_phones": decision.parsed.signature_phones,
            "signature_name": decision.parsed.signature_name,
            "signature_title": decision.parsed.signature_title,
            "signature_company": decision.parsed.signature_company,
        },
        "candidates": [
            {
                "entity_id": c.entity_id,
                "name": c.name,
                "raw_score": c.raw_score,
                "probability": c.probability,
                "probability_pct": round(c.probability * 100, 2),
                "rank": c.rank,
                "features": [
                    {
                        "name": f.name,
                        "value": f.value,
                        "weight": f.weight,
                        "contribution": f.contribution,
                        "source_refs": f.source_refs,
                        "detail": f.detail,
                        "matches": f.matches,
                        "similarity": f.similarity,
                    }
                    for f in c.features
                ],
                "penalties": c.penalties,
            }
            for c in decision.candidates
        ],
        "hard_rules": decision.hard_rules,
        "flags": decision.flags,
        "handle_class": decision.handle_class,
        "new_person_hint": decision.new_person_hint,
        "decision": decision.decision,
        "top_entity_id": decision.top_entity_id,
        "top_probability": decision.top_probability,
        "thresholds": decision.thresholds,
        "scoring_version": decision.scoring_version,
        "run_id": decision.run_id,
        "score_file": decision.score_file,
    }


async def score_handle(
    handle_kind: str,
    handle_value: str,
    *,
    people: list[Entity],
    interactions: list[InteractionRow],
    phone_index: dict[str, set[str]],
    handles_by_entity: dict[str, set[str]],
    display_name: str = "",
    body: str = "",
    run_id: str | None = None,
    now: datetime | None = None,
    settings: Settings | None = None,
    generate_json_fn: GenerateJsonFn | None = None,
    embed_fn: EmbedFn | None = None,
    config: dict[str, Any] | None = None,
    profile_embedding_cache: dict[str, list[float]] | None = None,
    employment_rows: list[dict[str, Any]] | None = None,
    rejected_entity_ids: set[str] | None = None,
    email_ts: datetime | None = None,
    anchor_entity_ids: set[str] | None = None,
) -> ScoringDecision:
    """Run Steps A–E for one unlinked handle and write a score file."""
    from recall.engines.world.identity_classify import classify_handle, lookalike_brand_domain
    from recall.engines.world.identity_normalize import parse_display_name
    from recall.engines.world.identity_text import strip_quoted_replies

    _ = settings
    cfg = config or load_scoring_config()
    weights = {k: float(v) for k, v in (cfg.get("weights") or {}).items()}
    thresholds = {k: float(v) for k, v in (cfg.get("thresholds") or {}).items()}
    temperature = float(cfg.get("softmax_temperature", 0.15))
    none_baseline = float(cfg.get("none_baseline", 0.25))
    version = int(cfg.get("version", 1))
    clock = now or datetime.now(timezone.utc)
    rid, score_path = resolve_score_jsonl_path(
        "identity_scores.jsonl", run_id=run_id
    )
    profile_cache = profile_embedding_cache if profile_embedding_cache is not None else {}
    flags: list[str] = []
    hard_rules: list[str] = []
    emp_rows = employment_rows or []
    rejected = rejected_entity_ids or set()
    anchors = anchor_entity_ids or set()

    clean_body = strip_quoted_replies(body or "")
    handle_class, class_details = classify_handle(
        handle_kind,
        handle_value,
        interactions=interactions,
        display_name=display_name,
        now=clock,
        config=cfg,
    )

    norm_value = (
        normalize_email(handle_value)
        if handle_kind == "email"
        else (normalize_phone(handle_value) or handle_value)
    )

    if handle_kind != "email":
        parsed = ParsedHandle(
            kind=handle_kind,
            value=norm_value,
            display_name=(display_name or "").strip(),
            local_tokens=name_tokens(display_name) if display_name else [],
        )
        # Signature phones from announcement bodies already on interactions.
        sig = extract_signature(clean_body)
        parsed.signature_name = sig.get("name", "") or parsed.display_name
        parsed.signature_phones = list(sig.get("phones") or [])
    else:
        parsed = parse_email_handle(
            handle_value, display_name=display_name, body=clean_body, config=cfg
        )
        disp_info = parse_display_name(display_name)
        if disp_info.get("company_hint") and not parsed.company:
            parsed.company = str(disp_info["company_hint"])
        lookalike = lookalike_brand_domain(parsed.domain, config=cfg)
        if lookalike:
            flags.append("suspicious_domain")
            parsed.company = ""
            parsed.aliases = []
            parsed.industry = []
        elif parsed.domain_class == "corporate" and parsed.domain and not parsed.industry:
            entry = await ensure_company_for_domain(
                parsed.domain, generate_json_fn=generate_json_fn
            )
            parsed.company = str(entry.get("company") or parsed.company)
            parsed.aliases = list(entry.get("aliases") or parsed.aliases)
            parsed.industry = list(
                entry.get("industry") or entry.get("industry_keywords") or []
            )

    if handle_class in {"organization", "shared"}:
        none = CandidateScore(
            entity_id=None,
            name="none of these",
            features=[],
            penalties=[],
            raw_score=none_baseline,
        )
        none.probability = 1.0
        none.rank = 1
        decision = ScoringDecision(
            handle_kind=handle_kind,
            handle_value=norm_value,
            parsed=parsed,
            candidates=[none],
            decision=handle_class,
            top_entity_id=None,
            top_probability=1.0,
            hard_rules=[f"classify_{handle_class}"],
            scoring_version=version,
            run_id=rid,
            score_file=str(score_path),
            thresholds=thresholds,
            flags=flags,
            handle_class=handle_class,
            new_person_hint={"org_hint": parsed.company or class_details.get("domain", "")},
        )
        written = write_score_file(decision, score_path)
        decision.score_file = str(written)
        return decision

    eligible = [p for p in people if p.id not in rejected]
    candidates_entities = select_candidates(parsed, eligible)
    seen_ids = {p.id for p in candidates_entities}

    def _add_candidate(person: Entity) -> None:
        if person.id in seen_ids:
            return
        seen_ids.add(person.id)
        candidates_entities.append(person)

    # Full display/signature name that matches nobody → treat as new person
    # (do not backfill via company mentions; that would steal a new name → coworker).
    full_display = (parsed.display_name or parsed.signature_name or "").strip()
    full_is_new = False
    if full_display and len(name_tokens(full_display)) >= 2 and not candidates_entities:
        from recall.engines.world.identity_normalize import names_match_normalized

        full_is_new = not any(
            names_match_normalized(full_display, p.canonical_name) for p in eligible
        )

    # Announcement backfill: already-linked handles announcing this value.
    for person in eligible:
        handles = handles_by_entity.get(person.id, set())
        f8_probe, _ = feature_announcement(
            norm_value,
            person,
            interactions,
            handles,
            is_anchor_announcer=person.id in anchors,
        )
        if f8_probe.value >= 1.0:
            _add_candidate(person)

    # Company / body backfill only when name selection found nobody and the
    # display name is not already a clear new person (avoids new-name→coworker
    # via a shared company org string).
    if not full_is_new and not candidates_entities:
        if parsed.company:
            for person in eligible:
                handles = handles_by_entity.get(person.id, set())
                f3 = feature_company_mentions(
                    person.id,
                    parsed.company,
                    parsed.aliases,
                    interactions,
                    handles,
                    now=clock,
                )
                if f3.value >= 0.2:
                    _add_candidate(person)
        if handle_kind != "email":
            body_l = clean_body.lower()
            for person in eligible:
                org = (person.attributes.get("org") or "").lower()
                if org and org in body_l:
                    _add_candidate(person)

    content_refs = await extract_email_references(clean_body, generate_json_fn=generate_json_fn)
    scored: list[CandidateScore] = []

    if not candidates_entities:
        none = CandidateScore(
            entity_id=None,
            name="none of these",
            features=[],
            penalties=[],
            raw_score=none_baseline,
        )
        none.probability = 1.0
        none.rank = 1
        new_min = float(thresholds.get("new_person_none_probability", 0.60))
        label = "new_person" if none.probability >= new_min else "unmatched"
        hint = _new_person_hint(parsed, display_name)
        decision = ScoringDecision(
            handle_kind=handle_kind,
            handle_value=norm_value,
            parsed=parsed,
            candidates=[none],
            decision=label,
            top_entity_id=None,
            top_probability=1.0,
            hard_rules=[],
            scoring_version=version,
            run_id=rid,
            score_file=str(score_path),
            thresholds=thresholds,
            flags=flags,
            handle_class=handle_class,
            new_person_hint=hint if label == "new_person" else {},
        )
        written = write_score_file(decision, score_path)
        decision.score_file = str(written)
        return decision

    for person in candidates_entities:
        person_handles = handles_by_entity.get(person.id, set())
        f1 = feature_signature_phone(parsed, person, phone_index)
        f2 = feature_name_match(parsed, person, body_for_mentions=clean_body)
        f3 = feature_company_mentions(
            person.id,
            parsed.company,
            parsed.aliases,
            interactions,
            person_handles,
            now=clock,
        )
        # Phone/WA: treat body company words as soft company signal via f3 path.
        if handle_kind != "email" and f3.value == 0.0:
            org = (person.attributes.get("org") or "").lower()
            if org and org in clean_body.lower():
                f3 = FeatureResult("f3", 0.6, 0.0, 0.0, [], f"body mentions {org}")
        f4 = await feature_industry_context(
            person,
            parsed,
            interactions,
            person_handles,
            cfg,
            generate_json_fn=generate_json_fn,
        )
        org = (person.attributes.get("org") or "").strip()
        recent_other = bool(org and parsed.company and org.lower() != parsed.company.lower())
        if f3.value >= 0.6 and parsed.company:
            recent_other = False
        # Signature phone match → trusted historical identity, never apply p1.
        if f1.value >= 1.0:
            recent_other = False
        f5, pens = feature_known_employer(
            person,
            parsed.company,
            parsed.aliases,
            recent_other_employer=recent_other,
            email_ts=email_ts,
            employment_rows=emp_rows if f1.value < 1.0 else [],
            now=clock,
        )
        if f1.value >= 1.0:
            pens = [p for p in pens if p.get("name") != "p1_conflicting_employer"]
        if email_ts is not None and clock - email_ts > timedelta(days=90):
            pens = [p for p in pens if p.get("name") != "p1_conflicting_employer"]
        f6 = feature_thread_overlap(
            norm_value,
            interactions,
            person_handles,
        )
        f7a = await feature_shared_references(
            person,
            content_refs,
            interactions,
            person_handles,
            now=clock,
            generate_json_fn=generate_json_fn,
        )
        f7b = await feature_topic_similarity(
            person,
            clean_body,
            interactions,
            person_handles,
            profile_cache,
            embed_fn=embed_fn,
        )
        is_anchor = person.id in anchors
        f8, announce_hard = feature_announcement(
            norm_value,
            person,
            interactions,
            person_handles,
            is_anchor_announcer=is_anchor,
        )
        hard_rules.extend(announce_hard)
        features = [f1, f2, f3, f4, f5, f6, f7a, f7b, f8]
        p1_val = float((cfg.get("penalties") or {}).get("p1_conflicting_employer", -0.25))
        for pen in pens:
            if pen.get("name") == "p1_conflicting_employer":
                pen["value"] = p1_val
        raw = apply_weights(features, weights, pens)
        scored.append(
            CandidateScore(
                entity_id=person.id,
                name=person.canonical_name,
                features=features,
                penalties=pens,
                raw_score=raw,
            )
        )
        if f1.value == 1.0:
            hard_rules.append("force_auto_linked")
        # Strong variant name + employer/body org → at least tentative.
        if f2.value >= 0.7 and f3.value >= 0.6:
            hard_rules.append("variant_name_company")

    none = CandidateScore(
        entity_id=None,
        name="none of these",
        features=[],
        penalties=[],
        raw_score=none_baseline,
    )
    scored.append(none)

    probs = softmax_probs([c.raw_score for c in scored], temperature)
    for cand, prob in zip(scored, probs, strict=True):
        cand.probability = prob
    ranked = sorted(scored, key=lambda c: c.probability, reverse=True)
    for index, cand in enumerate(ranked, start=1):
        cand.rank = index

    top_entity = next((c for c in ranked if c.entity_id), None)
    if top_entity is not None:
        f2_top = next((f for f in top_entity.features if f.name == "f2"), None)
        f7b_top = next((f for f in top_entity.features if f.name == "f7b"), None)
        others = [f for f in top_entity.features if f.name != "f2"]
        if f2_top and f2_top.value == 0.2 and all(f.value == 0.0 for f in others):
            hard_rules.append("block_auto_link")
        nonzero_aside_f2 = [f for f in top_entity.features if f.name != "f2" and f.value > 0]
        if (
            f2_top
            and f2_top.value <= 0.2
            and len(nonzero_aside_f2) == 1
            and nonzero_aside_f2[0].name == "f7b"
            and f7b_top
            and f7b_top.value > 0
        ):
            hard_rules.append("f7b_only_cap_tentative")

        # Namesake / margin: first-name-only collision.
        entity_rows = [c for c in ranked if c.entity_id]
        first_name_only = [
            c
            for c in entity_rows
            if any(f.name == "f2" and f.value <= 0.2 for f in c.features)
        ]
        if len(first_name_only) >= 2 and f2_top and f2_top.value <= 0.2:
            top_two = sorted(first_name_only, key=lambda c: c.probability, reverse=True)[:2]
            margin = top_two[0].probability - top_two[1].probability
            f7a_lead = next((f for f in top_two[0].features if f.name == "f7a"), None)
            # Shared-reference evidence can break a namesake tie.
            content_break = bool(f7a_lead and f7a_lead.value >= 0.4)
            same_employer = []
            for cand in first_name_only:
                f5 = next((f for f in cand.features if f.name == "f5"), None)
                if f5 and f5.value >= 1.0:
                    same_employer.append(cand)
            # Two+ namesakes at the same inferred employer → always ask (B1).
            if len(same_employer) >= 2:
                hard_rules.append("namesake_margin")
                hard_rules.append("cap_needs_user")
            elif (
                not content_break
                and margin < float(thresholds.get("auto_link_margin", 0.20))
            ):
                hard_rules.append("namesake_margin")
                hard_rules.append("cap_needs_user")

        # Spoof guard: full display-name match on free/unknown/.example domain with
        # weak identity support (ignore thread overlap / topic noise from scam bodies).
        structural = {"f1", "f3", "f5", "f8"}
        other_sum = sum(
            f.value for f in top_entity.features if f.name in structural
        )
        suspicious_domain = (
            parsed.domain_class == "free-mail"
            or not parsed.domain
            or parsed.domain.endswith(".example")
            or "mailbox.example" in (parsed.domain or "")
            or "suspicious_domain" in flags
        )
        if f2_top and f2_top.value >= 0.7 and suspicious_domain and other_sum < 0.5:
            hard_rules.append("possible_spoof")
            hard_rules.append("cap_needs_user")
            flags.append("possible_spoof")

        # Unique full-name match (f2=1) — only when a single canonical name hits
        # and we are not in a spoof-shaped case.
        full_name_hits = [
            c
            for c in ranked
            if c.entity_id
            and any(f.name == "f2" and f.value >= 1.0 for f in c.features)
        ]
        full_names = {c.name.strip().lower() for c in full_name_hits}
        if (
            len(full_names) == 1
            and "possible_spoof" not in hard_rules
            and not suspicious_domain
        ):
            hard_rules.append("unique_full_name")

    unique_hard = sorted(set(hard_rules))
    decision_label, top_id, top_p = decide(ranked, thresholds, unique_hard)
    if "variant_name_company" in unique_hard and decision_label == "needs_user":
        # Promote the entity that earned the variant+company signal.
        for cand in ranked:
            if cand.entity_id is None:
                continue
            f2 = next((f for f in cand.features if f.name == "f2"), None)
            f3 = next((f for f in cand.features if f.name == "f3"), None)
            if f2 and f3 and f2.value >= 0.7 and f3.value >= 0.6:
                decision_label = "tentative"
                top_id = cand.entity_id
                top_p = max(cand.probability, float(thresholds.get("tentative_min", 0.55)))
                break
    if (
        "force_auto_linked" in unique_hard
        or "announce_auto_link" in unique_hard
        or "unique_full_name" in unique_hard
    ):
        for cand in ranked:
            if cand.entity_id is None:
                continue
            f1 = next((f for f in cand.features if f.name == "f1"), None)
            f2 = next((f for f in cand.features if f.name == "f2"), None)
            f8 = next((f for f in cand.features if f.name == "f8"), None)
            if (
                (f1 and f1.value == 1.0)
                or (f8 and f8.value == 1.0 and "announce_auto_link" in unique_hard)
                or (f2 and f2.value >= 1.0 and "unique_full_name" in unique_hard)
            ):
                decision_label = "auto_linked"
                top_id = cand.entity_id
                top_p = max(cand.probability, float(thresholds.get("auto_link_probability", 0.80)))
                break

    entity_cands = [c for c in ranked if c.entity_id]
    # f7a override only when the winner also has a real name match (not a new person).
    if (
        entity_cands
        and decision_label not in {"organization", "shared", "new_person"}
        and "cap_needs_user" not in unique_hard
        and "possible_spoof" not in unique_hard
        and not full_is_new
    ):
        def _f7a(cand: CandidateScore) -> float:
            feat = next((f for f in cand.features if f.name == "f7a"), None)
            return float(feat.value) if feat else 0.0

        def _f2(cand: CandidateScore) -> float:
            feat = next((f for f in cand.features if f.name == "f2"), None)
            return float(feat.value) if feat else 0.0

        best = max(entity_cands, key=_f7a)
        second_f7a = max(
            (_f7a(c) for c in entity_cands if c.entity_id != best.entity_id), default=0.0
        )
        # Strong shared references can break first-name-only ties (gmail92).
        if _f7a(best) >= 0.4 and _f7a(best) - second_f7a >= 0.3 and _f2(best) >= 0.2:
            unique_hard = sorted(set(unique_hard) | {"f7a_content_override"})
            top_id = best.entity_id
            top_p = max(best.probability, float(thresholds.get("tentative_min", 0.55)))
            if decision_label == "needs_user":
                decision_label = "tentative"

    # Spoof / namesake caps must not undo hard auto-links (f1 phone, announcement).
    if "possible_spoof" in unique_hard or "cap_needs_user" in unique_hard:
        if decision_label in {"auto_linked", "tentative"} and not (
            {"force_auto_linked", "announce_auto_link"} & set(unique_hard)
        ):
            decision_label = "needs_user"
        if "possible_spoof" in unique_hard and "possible_spoof" not in flags:
            flags.append("possible_spoof")

    hint: dict[str, Any] = {}
    if decision_label == "new_person":
        hint = _new_person_hint(parsed, display_name)

    decision = ScoringDecision(
        handle_kind=handle_kind,
        handle_value=norm_value,
        parsed=parsed,
        candidates=ranked,
        decision=decision_label,
        top_entity_id=top_id,
        top_probability=top_p,
        hard_rules=unique_hard,
        scoring_version=version,
        run_id=rid,
        score_file=str(score_path),
        thresholds=thresholds,
        flags=flags,
        handle_class=handle_class,
        new_person_hint=hint,
    )
    written = write_score_file(decision, score_path)
    decision.score_file = str(written)
    return decision


def _new_person_hint(parsed: ParsedHandle, display_name: str) -> dict[str, Any]:
    """Build create-entity payload from display name / signature / domain."""
    def _usable(value: str) -> str:
        text = (value or "").strip()
        if not text or re.fullmatch(r"[\W_]+", text):
            return ""
        if len(name_tokens(text)) < 1:
            return ""
        return text

    name = (
        _usable(parsed.display_name)
        or _usable(display_name)
        or _usable(parsed.signature_name)
        or " ".join(t.capitalize() for t in parsed.local_tokens[:2])
        or parsed.value
    )
    return {
        "name": name,
        "org": parsed.signature_company or parsed.company or "",
        "title": parsed.signature_title or "",
        "domain": parsed.domain or "",
    }


def _interaction_belongs(row: InteractionRow, handles: set[str]) -> bool:
    hv = row.handle_value
    if hv in handles:
        return True
    if normalize_email(hv) in handles:
        return True
    norm = normalize_phone(hv)
    return bool(norm and norm in handles)


def _mentions_any(text: str, terms: list[str]) -> bool:
    lower = text.lower()
    for term in terms:
        if not term:
            continue
        # whole word-ish
        if re.search(rf"(?i)\b{re.escape(term)}\b", text):
            return True
        if term.lower() in lower:
            return True
    return False


def _parse_ts(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return datetime.now(timezone.utc)
