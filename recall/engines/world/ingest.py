"""Ingest source data into the world store via the query layer."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from rapidfuzz import fuzz

from recall.config import Settings, get_settings
from recall.data.queries import (
    ContactRow,
    HandleRow,
    InteractionRow,
    all_contacts,
    all_handles,
    all_interactions,
    resolve_now,
)
from recall.data.query_layer import QueryLayer, get_query_layer
from recall.engines.world.models import (
    AttributeObservation,
    Conflict,
    ConflictValue,
    Entity,
    Observation,
    Relationship,
)
from recall.engines.world.store import WorldStore, world_db_path
from recall.tracing import Tracer

_AGENT_ID = "world_ingest"
_CONFLICT_WINDOW_DAYS = 120
_NAME_FUZZ_THRESHOLD = 90

# Bump when attribute extraction rules change so sample DBs reingest.
INGEST_VERSION = "phase2b-meaningful-conflicts-v3"

# Fields where a contact-card value vs a newer message/signature is always a conflict.
_STRUCTURED_CONFLICT_FIELDS = frozenset({"city", "org", "phone"})

_PHONE_ANNOUNCE_RE = re.compile(
    r"(?i)\b("
    r"new number|nouveau num[eé]ro|save this (?:one|number)|"
    r"number is|mon nouveau"
    r")\b"
)
_EMPLOYER_CHAT_RE = re.compile(
    r"(?i)\b(?:started at|starting at|joined|works? at|working at|nouveau job chez)\s+"
    r"(?-i:([A-ZÀ-Ö][A-Za-zÀ-öø-ÿ0-9&.'\-]*(?:\s+[A-ZÀ-Ö][A-Za-zÀ-öø-ÿ0-9&.'\-]*){0,3}))"
)

_CITY_PATTERNS = [
    re.compile(
        r"(?im)(?:^|\n)\s*([A-Z][A-Za-z][A-Za-z\s\-']+),\s*(?:QC|ON|AB|BC|MB|SK|NS|NB|NL|PE|YT|NT|NU|NY|CA|TX|WA)\b"
    ),
    re.compile(r"(?i)\bmoving to ([A-Z][A-Za-z][A-Za-z\s\-']+?)(?:\s+in\b|[.,!]|$)"),
    re.compile(r"(?i)\bbased in ([A-Z][A-Za-z][A-Za-z\s\-']+?)(?:[.,!\n]|$)"),
    re.compile(r"(?i)\bin ([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)?),\s*(?:QC|ON)\b"),
    # FR/EN move announcements (city only — never org/title from chat bodies).
    re.compile(
        r"(?i)\b(?:d[ée]m[ée]nage(?:r)?|moved|moving)\s+(?:à|a|to)\s+"
        r"([A-ZÀ-Ö][A-Za-zÀ-öø-ÿ\-']+)"
    ),
    re.compile(
        r"(?i)\b(?:premier|first)\s+\w+\s+[àa]\s+"
        r"([A-ZÀ-Ö][A-Za-zÀ-öø-ÿ\-']+)"
    ),
]

# French/English function words that are never city names when captured alone.
_CITY_PARTICLES = frozenset({"le", "du", "la", "des", "de", "the", "a", "an"})

# Single-line email-signature "Title, Org" only (never chat / reply crumbs).
_TITLE_PATTERNS = [
    re.compile(
        r"(?im)^([A-Z][A-Za-z][A-Za-z /&-]{0,40}),[ \t]+"
        r"([A-Z][A-Za-z0-9][A-Za-z0-9 &.']{0,40})[ \t]*$"
    ),
]

_JUNK_ATTR_RE = re.compile(
    r"(?i)^(thanks?\b|perfect|parfait|yes|no|ok|on it|same|amazing|see you|"
    r"sending |will send|no worries|happy to|really needed|comments in|"
    r"door code|sunday is|grabbed a|mountain loop|what a |only the |"
    r"expired cert|in the security|v3 is|renewing )"
)

# "City, QC" style location lines must not be parsed as Title, Org.
_REGION_CODES = frozenset(
    {
        "QC",
        "ON",
        "AB",
        "BC",
        "MB",
        "SK",
        "NS",
        "NB",
        "NL",
        "PE",
        "YT",
        "NT",
        "NU",
        "NY",
        "CA",
        "TX",
        "WA",
        "USA",
        "UK",
    }
)


def normalize_name(name: str) -> str:
    """Lowercase, strip, collapse whitespace; keep letters/digits for id keys."""
    cleaned = re.sub(r"\s+", " ", (name or "").strip().lower())
    return cleaned


def normalize_phone(value: str) -> str:
    """Normalize phone / WhatsApp JID local-part to +E.164 digits when possible."""
    raw = (value or "").strip()
    if "@" in raw:
        raw = raw.split("@", 1)[0]
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return ""
    return f"+{digits}"


def email_domain(email: str) -> str:
    """Return the lowercase domain of an email, or empty."""
    if "@" not in email:
        return ""
    return email.rsplit("@", 1)[-1].strip().lower()


def stable_entity_id(canonical_name: str, emails: Iterable[str], phones: Iterable[str]) -> str:
    """Build ``ent_`` + sha1(normalized name + first email or phone)[:12]."""
    name_key = normalize_name(canonical_name)
    identifiers = [e.strip().lower() for e in emails if e and e.strip()]
    if not identifiers:
        identifiers = [normalize_phone(p) for p in phones if normalize_phone(p)]
    first = identifiers[0] if identifiers else ""
    digest = hashlib.sha1(f"{name_key}|{first}".encode("utf-8")).hexdigest()[:12]
    return f"ent_{digest}"


def _stable_rel_id(*parts: str) -> str:
    digest = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:12]
    return f"rel_{digest}"


def _stable_conflict_id(entity_id: str, field: str) -> str:
    digest = hashlib.sha1(f"{entity_id}|{field}".encode("utf-8")).hexdigest()[:12]
    return f"cnf_{digest}"


@dataclass
class PersonCluster:
    """Handles + optional contact card merged into one person."""

    handles: list[HandleRow] = field(default_factory=list)
    contacts: list[ContactRow] = field(default_factory=list)

    def emails(self) -> list[str]:
        found: list[str] = []
        for handle in self.handles:
            if handle.kind == "email" and handle.value:
                found.append(handle.value.lower())
        for contact in self.contacts:
            found.extend(e.lower() for e in contact.emails if e)
        # preserve order, unique
        seen: set[str] = set()
        out: list[str] = []
        for email in found:
            if email not in seen:
                seen.add(email)
                out.append(email)
        return out

    def phones(self) -> list[str]:
        found: list[str] = []
        for handle in self.handles:
            if handle.kind in {"phone", "imessage", "wa_jid"}:
                phone = normalize_phone(handle.value)
                if phone:
                    found.append(phone)
        for contact in self.contacts:
            for phone in contact.phones:
                norm = normalize_phone(phone)
                if norm:
                    found.append(norm)
        seen: set[str] = set()
        out: list[str] = []
        for phone in found:
            if phone not in seen:
                seen.add(phone)
                out.append(phone)
        return out

    def names(self) -> list[str]:
        names: list[str] = []
        for contact in self.contacts:
            if contact.full_name.strip():
                names.append(contact.full_name.strip())
        for handle in self.handles:
            if handle.display_name.strip():
                names.append(handle.display_name.strip())
        seen: set[str] = set()
        out: list[str] = []
        for name in names:
            key = normalize_name(name)
            if key and key not in seen:
                seen.add(key)
                out.append(name)
        return out

    def canonical_name(self) -> str:
        # Prefer a contact full_name without emoji-only nicknames.
        for contact in self.contacts:
            if contact.full_name.strip():
                return contact.full_name.strip()
        for name in self.names():
            if re.search(r"[A-Za-z]", name) and len(name.split()) >= 2:
                return name
        return self.names()[0] if self.names() else "Unknown"

    def orgs(self) -> list[str]:
        return [c.org.strip() for c in self.contacts if c.org.strip()]

    def domains(self) -> set[str]:
        domains = {email_domain(e) for e in self.emails() if email_domain(e)}
        for org in self.orgs():
            # crude: org name token as weak domain stand-in is NOT used for merge;
            # only email domains count for the shared-domain rule.
            _ = org
        return domains


class _UnionFind:
    def __init__(self, n: int) -> None:
        self.parent = list(range(n))

    def find(self, i: int) -> int:
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def cluster_people(
    handles: list[HandleRow],
    contacts: list[ContactRow],
) -> list[PersonCluster]:
    """Cluster handles into people by email/phone, then fuzzy name + shared domain/org."""
    # Seed one cluster slot per contact and per orphan handle group via UF on an index space.
    nodes: list[dict[str, Any]] = []
    # Contact nodes
    for contact in contacts:
        nodes.append({"kind": "contact", "contact": contact, "handles": []})
    # Handle nodes (may later attach to contacts)
    for handle in handles:
        nodes.append({"kind": "handle", "handle": handle})

    n = len(nodes)
    uf = _UnionFind(n)

    def contact_index(contact_id: str) -> int | None:
        for i, node in enumerate(nodes):
            if node["kind"] == "contact" and node["contact"].contact_id == contact_id:
                return i
        return None

    # Index emails / phones -> node indices
    email_index: dict[str, list[int]] = {}
    phone_index: dict[str, list[int]] = {}

    for i, node in enumerate(nodes):
        if node["kind"] == "contact":
            contact: ContactRow = node["contact"]
            for email in contact.emails:
                email_index.setdefault(email.lower(), []).append(i)
            for phone in contact.phones:
                norm = normalize_phone(phone)
                if norm:
                    phone_index.setdefault(norm, []).append(i)
        else:
            handle: HandleRow = node["handle"]
            if handle.kind == "email":
                email_index.setdefault(handle.value.lower(), []).append(i)
            elif handle.kind in {"phone", "imessage", "wa_jid"}:
                norm = normalize_phone(handle.value)
                if norm:
                    phone_index.setdefault(norm, []).append(i)

    for indices in list(email_index.values()) + list(phone_index.values()):
        for j in range(1, len(indices)):
            uf.union(indices[0], indices[j])

    # Fuzzy name merge: only between clusters that don't already share identity,
    # require token_set_ratio >= 90 AND shared org or email domain.
    # Never merge on first name alone (require >=2 name tokens match signal via threshold + shared org/domain).
    def cluster_name(root: int) -> str:
        names: list[str] = []
        for i, node in enumerate(nodes):
            if uf.find(i) != root:
                continue
            if node["kind"] == "contact":
                names.append(node["contact"].full_name)
            else:
                names.append(node["handle"].display_name)
        # Prefer longest multi-token name
        names = [n for n in names if n and n.strip()]
        names.sort(key=lambda n: (len(n.split()), len(n)), reverse=True)
        return names[0] if names else ""

    def cluster_orgs_domains(root: int) -> tuple[set[str], set[str]]:
        orgs: set[str] = set()
        domains: set[str] = set()
        for i, node in enumerate(nodes):
            if uf.find(i) != root:
                continue
            if node["kind"] == "contact":
                c = node["contact"]
                if c.org.strip():
                    orgs.add(c.org.strip().lower())
                for email in c.emails:
                    d = email_domain(email)
                    if d:
                        domains.add(d)
            else:
                h = node["handle"]
                if h.kind == "email":
                    d = email_domain(h.value)
                    if d:
                        domains.add(d)
        return orgs, domains

    roots = sorted({uf.find(i) for i in range(n)})
    for a_idx, root_a in enumerate(roots):
        for root_b in roots[a_idx + 1 :]:
            name_a = cluster_name(root_a)
            name_b = cluster_name(root_b)
            if not name_a or not name_b:
                continue
            # Never merge on first name alone: both must be multi-token OR fuzzy on full strings with shared org/domain.
            tokens_a = [t for t in re.split(r"\s+", normalize_name(name_a)) if t]
            tokens_b = [t for t in re.split(r"\s+", normalize_name(name_b)) if t]
            if len(tokens_a) < 2 and len(tokens_b) < 2:
                continue
            score = fuzz.token_set_ratio(name_a, name_b)
            if score < _NAME_FUZZ_THRESHOLD:
                continue
            orgs_a, domains_a = cluster_orgs_domains(root_a)
            orgs_b, domains_b = cluster_orgs_domains(root_b)
            shared_org = bool(orgs_a & orgs_b)
            shared_domain = bool(domains_a & domains_b)
            if not (shared_org or shared_domain):
                continue
            # Extra guard: if the only overlapping token is a common first name and surnames differ, skip
            if len(tokens_a) >= 2 and len(tokens_b) >= 2 and tokens_a[0] == tokens_b[0] and tokens_a[-1] != tokens_b[-1]:
                if score < 98:
                    continue
            uf.union(root_a, root_b)

    grouped: dict[int, PersonCluster] = {}
    for i, node in enumerate(nodes):
        root = uf.find(i)
        cluster = grouped.setdefault(root, PersonCluster())
        if node["kind"] == "contact":
            cluster.contacts.append(node["contact"])
        else:
            cluster.handles.append(node["handle"])

    # Drop empty-ish clusters (no name signal)
    people = [c for c in grouped.values() if c.names() or c.emails() or c.phones()]
    return people


def _normalize_attr_value(value: str) -> str:
    """Trim and collapse internal whitespace for storage / matching."""
    return re.sub(r"[ \t]+", " ", (value or "").strip())


def _attr_conflict_key(value: str) -> str:
    """Case-insensitive key used when deciding whether two values conflict."""
    return _normalize_attr_value(value).casefold()


def _is_plausible_structured_attr(value: str, *, field: str) -> bool:
    """Reject conversational crumbs mistakenly parsed as title/org/city."""
    text = (value or "").strip()
    if len(text) < 2 or len(text) > 48:
        return False
    if _JUNK_ATTR_RE.search(text):
        return False
    if text.endswith("?") or text.count(",") > 1:
        return False
    if field in {"org", "title"} and text[0].islower():
        return False
    if field == "title" and len(text.split()) > 6:
        return False
    if field == "org" and ("(" in text or text.lower().startswith("thanks")):
        return False
    return True


def extract_signature_attributes(
    text: str,
    *,
    source: str = "gmail",
) -> dict[str, str]:
    """Extract city freely; title/org only from gmail signature-like lines.

    Never pull title/org from imessage / whatsapp / messenger bodies.
    """
    found: dict[str, str] = {}
    if not text:
        return found
    for pattern in _CITY_PATTERNS:
        match = pattern.search(text)
        if match:
            city = _normalize_attr_value(match.group(1).strip(" .,!"))
            if not city or "\n" in city or len(city) >= 40:
                continue
            # First Capitalized token; drop trailing particles ("X le" → "X").
            tokens = re.findall(r"[A-Za-zÀ-öø-ÿ']+", city)
            picked = ""
            for tok in tokens:
                if tok.casefold() in _CITY_PARTICLES:
                    continue
                if tok[:1].isupper() or (tok[:1].isalpha() and tok.casefold() not in _CITY_PARTICLES):
                    picked = tok[0].upper() + tok[1:] if tok else tok
                    break
            if picked and picked.casefold() not in _CITY_PARTICLES:
                if _is_plausible_structured_attr(picked, field="city"):
                    found["city"] = picked
                    break

    if source not in {"gmail"}:
        return found

    # Signature block: last non-empty lines only (never the reply body).
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    signature_lines = lines[-8:] if len(lines) > 8 else lines
    for line_stripped in reversed(signature_lines):
        if any(pattern.search(line_stripped) for pattern in _CITY_PATTERNS):
            continue
        for pattern in _TITLE_PATTERNS:
            match = pattern.search(line_stripped)
            if not match:
                continue
            title = _normalize_attr_value(match.group(1))
            org = _normalize_attr_value(match.group(2))
            if org.upper() in _REGION_CODES:
                continue
            # "Founder & CEO" → core title "Founder" (avoid title conflicts).
            if "&" in title:
                title = title.split("&", 1)[0].strip()
            if not _is_plausible_structured_attr(title, field="title"):
                continue
            if not _is_plausible_structured_attr(org, field="org"):
                continue
            if title and "\n" not in title and not found.get("title"):
                found["title"] = title
            if org and "\n" not in org and not found.get("org"):
                found["org"] = org
            break
        if found.get("title") and found.get("org"):
            break
    return found


def _parse_ts(value: str) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return datetime.now(timezone.utc)


def _fmt(ts: datetime) -> str:
    return ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class IngestPipelineService:
    """Idempotent, resumable world ingest over the query layer."""

    def __init__(
        self,
        store: WorldStore | None = None,
        settings: Settings | None = None,
        tracer: Tracer | None = None,
    ) -> None:
        self._settings = settings if settings is not None else get_settings()
        self._store = store if store is not None else WorldStore(settings=self._settings)
        self._tracer = tracer if tracer is not None else Tracer.current()
        self._owns_store = store is None

    def run(self, query_layer: QueryLayer | None = None) -> dict[str, int]:
        """Pull contacts/handles, cluster, upsert entities, detect conflicts."""
        layer = query_layer if query_layer is not None else get_query_layer(self._settings)
        run_id = self._tracer.run_id if self._tracer is not None else "world-offline"
        provenance_path = Path(self._settings.logs_dir) / run_id / "world_provenance.jsonl"
        provenance_path.parent.mkdir(parents=True, exist_ok=True)

        stage = self._store.get_meta("ingest_stage")
        # Resumable: if a previous run finished, we still re-upsert (idempotent).
        _ = stage
        self._store.set_meta("ingest_stage", "started")

        handles = all_handles(source_tag="world.ingest.handles", layer=layer)
        contacts = all_contacts(source_tag="world.ingest.contacts", layer=layer)
        interactions = all_interactions(
            source_tag="world.ingest.interactions",
            since_days=400,
            limit=5000,
            settings=self._settings,
            layer=layer,
        )
        self._store.set_meta("ingest_stage", "loaded")

        people = cluster_people(handles, contacts)
        self._store.set_meta("ingest_stage", "clustered")

        entity_count = 0
        conflict_count = 0
        observation_count = 0
        now = resolve_now(self._settings)

        for person in people:
            entity, observations, conflicts, relationships = self._build_person(
                person,
                interactions=interactions,
                run_id=run_id,
                now=now,
            )
            self._store.upsert_entity(entity)
            entity_count += 1
            self._store.clear_conflicts_for_entity(entity.id)
            for conflict in conflicts:
                self._store.upsert_conflict(conflict)
                conflict_count += 1
            for observation in observations:
                self._store.append_observation(observation)
                observation_count += 1
                self._write_provenance(provenance_path, observation)
            for rel in relationships:
                self._store.upsert_relationship(rel)

        self._store.set_meta("ingest_stage", "identity")
        identity_counts = self._run_identity_scoring(run_id=run_id, layer=layer)

        self._store.set_meta("ingest_stage", "name_merge")
        from recall.engines.world.name_merge import run_name_merge

        merge_counts = run_name_merge(
            store=self._store,
            settings=self._settings,
            interactions=interactions,
            tracer=self._tracer,
            run_id=run_id,
        )
        # Merges combine attribute histories; refresh structured conflicts so
        # card-vs-new-phone (etc.) still surface after a name_merge join.
        conflict_count = self._refresh_conflicts_from_history()

        self._store.set_meta("ingest_stage", "relationships")
        relationship_counts = self._run_relationship_scoring(run_id=run_id, layer=layer)

        self._store.set_meta("ingest_stage", "done")
        self._store.set_meta("last_run_id", run_id)
        if self._settings.sample_mode:
            from recall.engines.world.store import source_tables_fingerprint

            self._store.set_meta(
                "source_fingerprint",
                source_tables_fingerprint(self._settings.sample_data_dir),
            )

        counts = {
            "entities": entity_count,
            "conflicts": conflict_count,
            "observations": observation_count,
            "handles_in": len(handles),
            "contacts_in": len(contacts),
            "identity_scored": int(identity_counts.get("scored", 0)),
            "relationships_scored": int(relationship_counts.get("scored", 0)),
            "merged": int(merge_counts.get("merged", 0)),
            "merge_questions": int(merge_counts.get("asked", 0)),
        }
        if self._tracer is not None:
            self._tracer.event("world", "ingest", counts)
        return counts

    def _run_relationship_scoring(
        self, *, run_id: str, layer: QueryLayer
    ) -> dict[str, int]:
        """Classify relationship labels after identity resolution."""
        from recall.data.duckdb_backend import DuckDBBackend
        from recall.engines.world.relationship import assess_all_people

        DuckDBBackend.clear_connection_cache()
        return assess_all_people(
            store=self._store,
            settings=self._settings,
            query_layer=get_query_layer(self._settings, tracer=self._tracer),
            tracer=self._tracer,
            run_id=run_id,
        )

    def _run_identity_scoring(self, *, run_id: str, layer: QueryLayer) -> dict[str, int]:
        """Score unlinked handles once after entities are written."""
        import asyncio

        from recall.data.duckdb_backend import DuckDBBackend
        from recall.engines.world.identity_service import IdentityService

        # Ensure CSV edits are visible even if a prior DuckDB connection was cached.
        DuckDBBackend.clear_connection_cache()
        service = IdentityService(
            store=self._store,
            settings=self._settings,
            tracer=self._tracer,
            query_layer=get_query_layer(self._settings, tracer=self._tracer),
        )
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None and loop.is_running():
            # Ingest is sync; schedule a dedicated loop when already inside async.
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(lambda: asyncio.run(service.run_for_ingest(run_id=run_id))).result()
        return asyncio.run(service.run_for_ingest(run_id=run_id))

    def _build_person(
        self,
        person: PersonCluster,
        *,
        interactions: list[InteractionRow],
        run_id: str,
        now: datetime,
    ) -> tuple[Entity, list[Observation], list[Conflict], list[Relationship]]:
        canonical = person.canonical_name()
        emails = person.emails()
        phones = person.phones()
        entity_id = stable_entity_id(canonical, emails, phones)

        aliases: list[str] = []
        for name in person.names():
            if name not in aliases:
                aliases.append(name)
        for email in emails:
            if email not in aliases:
                aliases.append(email)
        for phone in phones:
            if phone not in aliases:
                aliases.append(phone)
        for handle in person.handles:
            if handle.value and handle.value not in aliases:
                aliases.append(handle.value)

        history: list[AttributeObservation] = []
        observations: list[Observation] = []

        def observe(field: str, value: str, observed_at: str, source_ref: str) -> None:
            cleaned = _normalize_attr_value(value)
            if not cleaned or "\n" in cleaned:
                return
            history.append(
                AttributeObservation(
                    field=field,
                    value=cleaned,
                    observed_at=observed_at,
                    source_ref=source_ref,
                )
            )
            observations.append(
                Observation(
                    entity_id=entity_id,
                    field=field,
                    value=cleaned,
                    source_ref=source_ref,
                    agent_id=_AGENT_ID,
                    run_id=run_id,
                    ts=observed_at,
                )
            )

        # Contact card attributes — dated well before recent messages so a
        # newer gmail signature / move announcement wins (stale-card note).
        for contact in person.contacts:
            source_ref = f"contact:{contact.contact_id}"
            contact_ts = _fmt(now - timedelta(days=365))
            if contact.org:
                observe("org", contact.org, contact_ts, source_ref)
            if contact.title:
                observe("title", contact.title, contact_ts, source_ref)
            if contact.city:
                observe("city", contact.city, contact_ts, source_ref)
            nick = (getattr(contact, "nickname", None) or "").strip()
            if nick:
                observe("nickname", nick, contact_ts, source_ref)
                if nick not in aliases and nick.lower() not in {
                    a.lower() for a in aliases
                }:
                    aliases.append(nick)
            for phone in contact.phones:
                norm = normalize_phone(phone)
                if norm:
                    observe("phone", norm, contact_ts, source_ref)

        # Handle values as identifiers
        handle_values = {h.value for h in person.handles} | set(emails) | set(phones)
        # Also match wa_jid by phone digits
        phone_digits = {normalize_phone(p) for p in phones}

        related_interactions = [
            row
            for row in interactions
            if row.handle_value in handle_values
            or normalize_phone(row.handle_value) in phone_digits
        ]
        related_interactions.sort(key=lambda r: r.ts_utc)

        for row in related_interactions:
            extracted = extract_signature_attributes(row.text, source=row.source)
            for field_name, value in extracted.items():
                observe(field_name, value, row.ts_utc, row.source_ref)
            # Chat job-change announcements (employer only — never title crumbs).
            if row.source in {"imessage", "whatsapp", "messenger", "gmail"}:
                emp = _EMPLOYER_CHAT_RE.search(row.text or "")
                if emp:
                    org = _normalize_attr_value(emp.group(1).strip(" .,!"))
                    if _is_plausible_structured_attr(org, field="org"):
                        observe("org", org, row.ts_utc, row.source_ref)
            # Self-announced new phone number (handle on the message is the new one).
            if _PHONE_ANNOUNCE_RE.search(row.text or ""):
                announced = normalize_phone(row.handle_value)
                if announced:
                    observe("phone", announced, row.ts_utc, row.source_ref)
        # Canonical attributes = latest per field
        attributes: dict[str, str] = {}
        by_field: dict[str, list[AttributeObservation]] = {}
        for item in history:
            by_field.setdefault(item.field, []).append(item)
        for field_name, items in by_field.items():
            items_sorted = sorted(items, key=lambda x: x.observed_at)
            attributes[field_name] = items_sorted[-1].value

        conflicts = self._detect_conflicts(entity_id, by_field)

        first_seen = related_interactions[0].ts_utc if related_interactions else _fmt(now)
        last_seen = related_interactions[-1].ts_utc if related_interactions else first_seen
        if person.contacts and (not related_interactions):
            first_seen = _fmt(now - timedelta(days=60))
            last_seen = first_seen

        entity = Entity(
            id=entity_id,
            type="person",
            canonical_name=canonical,
            aliases=aliases,
            attributes=attributes,
            attribute_history=history,
            first_seen_at=first_seen,
            last_seen_at=last_seen,
            mention_count=max(len(related_interactions), 1),
            confidence=0.8 if person.contacts else 0.6,
            agent_id=_AGENT_ID,
            run_id=run_id,
        )

        relationships: list[Relationship] = []
        org = attributes.get("org")
        if org:
            org_id = stable_entity_id(org, [], [])
            # Ensure org entity exists lightly
            org_entity = Entity(
                id=org_id,
                type="org",
                canonical_name=org,
                aliases=[org],
                attributes={},
                attribute_history=[],
                first_seen_at=first_seen,
                last_seen_at=last_seen,
                mention_count=1,
                confidence=0.5,
                agent_id=_AGENT_ID,
                run_id=run_id,
            )
            self._store.upsert_entity(org_entity)
            relationships.append(
                Relationship(
                    id=_stable_rel_id(entity_id, "works_at", org_id),
                    from_id=entity_id,
                    to_id=org_id,
                    type="works_at",
                    valid_from=first_seen,
                    source_ref=next(
                        (h.source_ref for h in history if h.value == org and h.source_ref),
                        "",
                    ),
                    confidence=0.7,
                )
            )

        return entity, observations, conflicts, relationships

    def _refresh_conflicts_from_history(self) -> int:
        """Recompute conflicts from each person's attribute_history (post-merge)."""
        total = 0
        for entity in self._store.list_entities(type="person"):
            by_field: dict[str, list[AttributeObservation]] = {}
            for item in entity.attribute_history or []:
                by_field.setdefault(item.field, []).append(item)
            conflicts = self._detect_conflicts(entity.id, by_field)
            self._store.clear_conflicts_for_entity(entity.id)
            for conflict in conflicts:
                self._store.upsert_conflict(conflict)
                total += 1
            # Keep attributes consistent with newest observation per field.
            if by_field:
                attrs = dict(entity.attributes or {})
                for field_name, items in by_field.items():
                    items_sorted = sorted(items, key=lambda x: x.observed_at)
                    attrs[field_name] = items_sorted[-1].value
                if attrs != entity.attributes:
                    entity.attributes = attrs
                    self._store.upsert_entity(entity)
        return total

    def _detect_conflicts(
        self,
        entity_id: str,
        by_field: dict[str, list[AttributeObservation]],
    ) -> list[Conflict]:
        conflicts: list[Conflict] = []
        for field_name, items in by_field.items():
            by_key: dict[str, list[AttributeObservation]] = {}
            for item in items:
                key = _attr_conflict_key(item.value)
                if not key:
                    continue
                by_key.setdefault(key, []).append(item)
            if len(by_key) < 2:
                continue
            keys = list(by_key.keys())
            conflicted = False
            for i, key_a in enumerate(keys):
                for key_b in keys[i + 1 :]:
                    for left in by_key[key_a]:
                        for right in by_key[key_b]:
                            if self._observations_conflict(field_name, left, right):
                                conflicted = True
                                break
                        if conflicted:
                            break
                    if conflicted:
                        break
                if conflicted:
                    break
            if not conflicted:
                continue
            # Represent each distinct value by its earliest observation.
            values = [
                sorted(obs_list, key=lambda x: x.observed_at)[0]
                for obs_list in by_key.values()
            ]
            conflicts.append(
                Conflict(
                    id=_stable_conflict_id(entity_id, field_name),
                    entity_id=entity_id,
                    field=field_name,
                    values=[
                        ConflictValue(
                            value=v.value,
                            source_ref=v.source_ref,
                            observed_at=v.observed_at,
                        )
                        for v in values
                    ],
                )
            )
        return conflicts

    @staticmethod
    def _observations_conflict(
        field_name: str,
        left: AttributeObservation,
        right: AttributeObservation,
    ) -> bool:
        """True when two observations of the same field meaningfully disagree."""
        delta = abs(
            (_parse_ts(left.observed_at) - _parse_ts(right.observed_at)).days
        )
        if delta <= _CONFLICT_WINDOW_DAYS:
            return True
        # Contact card vs a newer message/signature/domain observation: always
        # record for city / org / phone (stale cards are the point).
        if field_name not in _STRUCTURED_CONFLICT_FIELDS:
            return False
        left_card = str(left.source_ref or "").startswith("contact:")
        right_card = str(right.source_ref or "").startswith("contact:")
        return left_card != right_card

    @staticmethod
    def _write_provenance(path: Path, observation: Observation) -> None:
        record = {
            "ts": observation.ts,
            "entity_id": observation.entity_id,
            "field": observation.field,
            "value": observation.value,
            "source_ref": observation.source_ref,
            "agent_id": observation.agent_id,
            "run_id": observation.run_id,
        }
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def ingest(source: str = "query_layer") -> int:
    """Load the configured source into the world store. Returns entity count."""
    _ = source
    service = IngestPipelineService()
    counts = service.run()
    return int(counts.get("entities", 0))


def run_world_ingest(
    *,
    sample: bool,
    settings: Settings | None = None,
    tracer: Tracer | None = None,
) -> dict[str, int]:
    """CLI helper: build world_sample.db or world.db via the query layer."""
    import os

    if sample:
        os.environ["SAMPLE_MODE"] = "true"
    else:
        os.environ["SAMPLE_MODE"] = "false"
    get_settings.cache_clear()
    active = settings if settings is not None else get_settings()
    # Rebuild settings after env change when caller didn't pass one.
    if settings is None:
        active = get_settings()
    active_tracer = tracer if tracer is not None else Tracer.current()
    db_path = world_db_path(active)
    store = WorldStore(path=db_path, settings=active)
    layer = get_query_layer(active, tracer=active_tracer)
    service = IngestPipelineService(store=store, settings=active, tracer=active_tracer)
    return service.run(layer)
