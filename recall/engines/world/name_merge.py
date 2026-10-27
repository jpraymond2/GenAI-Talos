"""Post-ingest pass: decide when two person entities with the same or similar
name are really one contact — merge when the evidence is strong and
unambiguous; ask the user a yes/no question when the evidence is middling
or two contacts fit equally well.

Runs after identity scoring, before relationship scoring. Every decision is
explainable: it records a score, the reasons for and against, and the outcome
(merged / ask / kept_apart) with a confidence in the ``merge_decisions`` table and in
``logs/<run_id>/merge_decisions.jsonl``.

How a pair is judged
--------------------
1. Same phone or email on both entities  -> always the same person.
2. Candidate pairs come from names only:
     exact same name                       base 0.75
     nickname of the same name + surname   base 0.65
     short form vs longer same first+last  base 0.60
     first-name-only vs a full name        base 0.55
     a bare phone number that introduces
     itself ("hey it's <name>, new number") base 0.50
3. Evidence moves the score:
     + one side's handle was announced in the other's messages ("new email is …")
     + they use different channels (contacts card vs Messenger) — typical of a split
     + email address spells the full name (local-part ↔ display name)
     + the name is unique among all contacts
     - both have their own, different phone numbers
     - both have work emails at different companies
     - different employers on their contact cards
     - one is a work-only contact, the other only chats socially
4. Decide:  >= 0.75 merge · 0.55–0.75 ask (an LLM may settle it in live mode) · below: keep apart.
   If another contact is within 0.10 and isn't the same person, ask instead of guessing.
   Never join two clusters when anyone in one is clearly not someone in the other.

Also retypes obvious non-people (newsletters, receipts, no-reply senders) from
person to org, so they stop showing up as contacts.
"""

from __future__ import annotations

import json
import logging
import math
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from recall.config import Settings
from recall.engines.world.identity_normalize import load_name_variants
from recall.engines.world.store import WorldStore
from recall.tracing import Tracer

logger = logging.getLogger(__name__)

MERGE_AT = 0.75  # strong enough to merge on its own
ASK_AT = 0.55  # middling: ask the user (or the LLM in live mode)
AMBIGUITY_MARGIN = 0.10  # a rival this close means "ask, don't guess"
CANNOT_LINK = 0.40  # strong evidence two contacts are different people (blocks joining their clusters)
SEPARATE_SCORE = 0.60  # displayed confidence = 1 / (1 + exp(-(score - 0.60) / 0.15))
TEMPERATURE = 0.15
TOPIC_SAME = 0.20  # cosine similarity of what two contacts talk about
TOPIC_UNRELATED = 0.03
_SERVICE_LOCALS = {
    "noreply", "no-reply", "donotreply", "do-not-reply", "notifications", "notification", "digest",
    "newsletter", "news", "nouvelles", "receipts", "receipt", "alerts", "alertes", "offers", "deals",
    "hello", "info", "team", "support", "billing", "calendar-notification", "updates", "studentaffairs",
}
_SERVICE_WORDS = {"newsletter", "digest", "weekly", "receipts", "services", "notifications", "calendar",
                  "alerts", "bulletin", "affairs", "banque", "bank"}
_FREE_MAIL = {"gmail.com", "outlook.com", "hotmail.com", "live.com", "icloud.com", "me.com", "yahoo.com",
              "proton.me", "protonmail.com", "aol.com", "videotron.ca"}
_SELF_INTRO = re.compile(r"\b(?:it'?s|this is|c'est|ici)\s+([A-ZÀ-Ý][\w'-]+)", re.IGNORECASE)
_NEW_HANDLE = ("new number", "new phone", "save this one", "save this number", "new email", "my email is",
               "nouveau numéro", "nouvelle adresse")


# --------------------------------------------------------------------------- names
def _fold(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9' -]", " ", plain.lower())).strip()


def clean_name(name: str) -> str:
    """'Alex B (MBA)' -> 'alex b'; accented names fold to ASCII lowercase."""
    return _fold(re.sub(r"\([^)]*\)", " ", name))


def _variants(first: str) -> set[str]:
    for group in load_name_variants():
        if first in group:
            return set(group)
    return {first}


def full_name_key(tokens: list[str]) -> str:
    """Nickname-equivalent first names + same surname share a key."""
    if len(tokens) < 2:
        return ""
    return "full:" + "/".join(sorted(_variants(tokens[0]))) + " " + tokens[-1]


def _looks_like_handle(name: str) -> bool:
    return "@" in name or bool(re.fullmatch(r"\+?[\d\s().-]{7,}", name.strip()))


def _local_tokens(email: str) -> list[str]:
    return [t for t in re.split(r"[._\-+0-9]+", _fold(email.split("@", 1)[0])) if t]


# --------------------------------------------------------------------------- entity view
@dataclass
class _View:
    id: str
    name: str
    tokens: list[str]
    emails: set[str] = field(default_factory=set)
    phones: set[str] = field(default_factory=set)
    channels: set[str] = field(default_factory=set)
    card_org: str = ""
    mentions: int = 0
    inbound_text: list[str] = field(default_factory=list)
    texts: list[str] = field(default_factory=list)  # whole conversation with this contact, both directions

    @property
    def first(self) -> str:
        return self.tokens[0] if self.tokens else ""

    @property
    def last(self) -> str:
        return self.tokens[-1] if len(self.tokens) > 1 else ""

    @property
    def is_bare_handle(self) -> bool:
        return _looks_like_handle(self.name)

    @property
    def work_domains(self) -> set[str]:
        return {e.split("@", 1)[1] for e in self.emails if e.split("@", 1)[1] not in _FREE_MAIL}


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value)


def _build_views(store: WorldStore, interactions: list[Any]) -> dict[str, _View]:
    conn = store._conn  # noqa: SLF001 — read-mostly helper living next to the store
    views: dict[str, _View] = {}
    for row in conn.execute("SELECT id, canonical_name, aliases_json, mention_count FROM entities WHERE type = 'person'"):
        aliases = json.loads(row["aliases_json"] or "[]") + [row["canonical_name"]]
        v = _View(id=row["id"], name=row["canonical_name"], tokens=clean_name(row["canonical_name"]).split(),
                  mentions=int(row["mention_count"] or 0))
        for a in aliases:
            a = str(a).strip()
            if a.endswith("@s.whatsapp.net"):  # a WhatsApp JID is a phone number
                v.phones.add("+" + _digits(a.split("@", 1)[0]))
            elif "@" in a:
                v.emails.add(a.lower())
            elif _looks_like_handle(a):
                v.phones.add("+" + _digits(a))
        views[v.id] = v
    for row in conn.execute("SELECT entity_id, handle_value FROM handle_links WHERE status IN ('active', 'new')"):
        v = views.get(row["entity_id"])
        if v is None:
            continue
        val = str(row["handle_value"])
        if "@" in val and not val.endswith("@s.whatsapp.net"):
            v.emails.add(val.lower())
        else:
            # Display-name messenger links have no digits — do not invent a
            # shared "+" phone that would merge every chat contact together.
            digits = _digits(val.split("@", 1)[0])
            if len(digits) >= 7:
                v.phones.add("+" + digits)
    for row in conn.execute("SELECT entity_id, value FROM observations WHERE field = 'org' AND source_ref LIKE 'contact:%'"):
        if row["entity_id"] in views:
            views[row["entity_id"]].card_org = str(row["value"])
    for row in conn.execute("SELECT DISTINCT entity_id FROM observations WHERE source_ref LIKE 'contact:%'"):
        if row["entity_id"] in views:
            views[row["entity_id"]].channels.add("contacts")

    # Channels + inbound text from the interactions each entity's handles/names appear in.
    index: dict[str, list[str]] = {}
    for v in views.values():
        # Contacts with a phone/email are found by handle; only name-only contacts (e.g. Messenger) by name,
        # otherwise two different people with the same name would share each other's conversations.
        by_name = {v.name.lower()} if not (v.emails or v.phones or v.is_bare_handle) else set()
        for key in v.emails | v.phones | by_name:
            index.setdefault(key, []).append(v.id)
    for it in interactions:
        hv = str(getattr(it, "handle_value", "") or "")
        key = hv.lower() if "@" in hv or not _looks_like_handle(hv) else "+" + _digits(hv)
        for eid in index.get(key, []):
            v = views[eid]
            v.channels.add(str(getattr(it, "source", "")))
            body = f"{getattr(it, 'subject', '') or ''} {getattr(it, 'text', '') or ''}".strip()
            if len(v.texts) < 800:
                v.texts.append(body)
            if getattr(it, "direction", "") == "in" and len(v.inbound_text) < 400:
                v.inbound_text.append(str(getattr(it, "text", "") or ""))
    return views


# --------------------------------------------------------------------------- what they talk about
_STOP = set("""
about above after again against also always another anyone anything around back been before being below between
both cannot could didn does doing done down during each even every from further have having here hers herself
him himself his into itself just know like make many more most much must never next only other ours over really
same should since some still such sure than that thank thanks that's their them then there these they thing
think this those though through today tomorrow tonight under until very want week well were what when where which
while will with would your yours yeah okay sounds great good time going coming tout tous toute avec dans pour
mais plus comme cette sont avoir être fait faire bien merci aussi encore alors donc juste quand quoi elle elles
nous vous leur leurs votre notre demain soir semaine
""".split())


def _stems(text: str) -> list[str]:
    words = re.findall(r"[a-zà-ÿ]{4,}", text.lower())
    return [_fold(w)[:6] for w in words if w not in _STOP]


class TopicModel:
    """TF-IDF over each contact's conversation. Words most contacts use get ~no weight,
    so similarity comes from specific shared topics (a wedding, a data room, a lease)."""

    def __init__(self, views: dict[str, _View]) -> None:
        self.vec: dict[str, dict[str, float]] = {}
        docs = {eid: _stems(" ".join(v.texts)) for eid, v in views.items() if v.texts}
        df: dict[str, int] = {}
        for words in docs.values():
            for w in set(words):
                df[w] = df.get(w, 0) + 1
        n = max(1, len(docs))
        for eid, words in docs.items():
            tf: dict[str, int] = {}
            for w in words:
                tf[w] = tf.get(w, 0) + 1
            vec = {w: (1 + math.log(c)) * math.log(n / df[w]) for w, c in tf.items() if df[w] / n <= 0.3}
            norm = math.sqrt(sum(x * x for x in vec.values())) or 1.0
            self.vec[eid] = {w: x / norm for w, x in vec.items()}

    def similarity(self, a: str, b: str) -> tuple[float, list[str]]:
        va, vb = self.vec.get(a, {}), self.vec.get(b, {})
        shared = sorted(set(va) & set(vb), key=lambda w: -(va[w] * vb[w]))
        return sum(va[w] * vb[w] for w in shared), shared[:4]


# --------------------------------------------------------------------------- scoring
@dataclass
class Judgement:
    a: str
    b: str
    score: float
    reasons_for: list[str]
    reasons_against: list[str]
    decision: str = ""


def _name_base(a: _View, b: _View, first_counts: dict[str, int]) -> tuple[float, str] | None:
    if a.is_bare_handle or b.is_bare_handle:
        bare, named = (a, b) if a.is_bare_handle else (b, a)
        if named.is_bare_handle:
            return None
        intro = {m.group(1).lower() for t in bare.inbound_text for m in _SELF_INTRO.finditer(t)}
        intro = {_fold(x) for x in intro}
        if named.first and (intro & _variants(named.first)):
            return 0.50, f"the unknown number introduces itself as '{named.first.title()}'"
        return None
    if not a.tokens or not b.tokens:
        return None
    if a.tokens == b.tokens:
        return 0.75, "exact same name"
    if a.last and b.last and a.last == b.last and a.first != b.first and b.first in _variants(a.first):
        return 0.65, f"'{a.first}' and '{b.first}' are forms of the same first name, same surname"
    if a.first == b.first and a.last and b.last and len(a.last) == 1 and b.last.startswith(a.last):
        return 0.60, f"'{a.name}' looks like an abbreviation of '{b.name}'"
    if a.first == b.first and a.last and b.last and len(b.last) == 1 and a.last.startswith(b.last):
        return 0.60, f"'{b.name}' looks like an abbreviation of '{a.name}'"
    if (len(a.tokens) == 1) != (len(b.tokens) == 1) and a.first == b.first:
        return 0.55, "one entry is saved by first name only"
    return None


def judge(a: _View, b: _View, first_counts: dict[str, int], topics: TopicModel | None = None) -> Judgement | None:
    base = _name_base(a, b, first_counts)
    if base is None:
        return None
    score, why = base
    pro, con = [why], []

    # Announced handle: one side's phone/email appears in the other's messages
    for x, y in ((a, b), (b, a)):
        texts = " ".join(x.inbound_text).lower()
        if any(e in texts for e in y.emails) or any(_digits(p)[-7:] in _digits(texts) for p in y.phones if len(_digits(p)) >= 7):
            score += 0.40
            pro.append(f"{x.name}'s messages mention {y.name}'s handle")
            break
    if a.is_bare_handle or b.is_bare_handle:
        bare = a if a.is_bare_handle else b
        if any(k in t.lower() for t in bare.inbound_text for k in _NEW_HANDLE):
            score += 0.35
            pro.append("the number says it is a new number")

    if a.channels and b.channels and not (a.channels & b.channels):
        score += 0.10
        pro.append(f"different channels ({', '.join(sorted(a.channels))} vs {', '.join(sorted(b.channels))}) — typical of one person split in two")

    for x, y in ((a, b), (b, a)):
        if len(y.tokens) >= 2 and any(_local_tokens(e)[:2] == y.tokens[:2] for e in x.emails):
            score += 0.25
            pro.append(f"{x.name}'s email spells '{y.name}'")
            break

    if "abbreviation" in why:
        full, short = (a, b) if len(a.last) > 1 else (b, a)
        if first_counts.get(f"{short.first} {short.last}", 0) <= 1:
            score += 0.10
            pro.append(f"only one '{short.first.title()}' whose surname starts with '{short.last.upper()}'")
    if not (a.is_bare_handle or b.is_bare_handle) and (len(a.tokens) == 1 or len(b.tokens) == 1) and first_counts.get(a.first, 0) <= 2:
        score += 0.15
        pro.append(f"only one '{a.first.title()}' among the contacts")

    if topics is not None and len(a.texts) >= 3 and len(b.texts) >= 3:
        sim, words = topics.similarity(a.id, b.id)
        if sim >= TOPIC_SAME:
            score += 0.15
            pro.append(f"they talk about the same specific things ({', '.join(words)}…; similarity {sim:.2f})")
        elif sim < TOPIC_UNRELATED and len(a.texts) >= 10 and len(b.texts) >= 10:
            score -= 0.10
            con.append(f"their conversations have almost nothing in common (similarity {sim:.2f})")

    pa, pb = a.phones - b.phones, b.phones - a.phones
    announced = any("mention" in r or "new number" in r for r in pro)
    if a.phones and b.phones and pa and pb and not announced:
        score -= 0.30
        con.append("each has its own, different phone number")
    if a.work_domains and b.work_domains and not (a.work_domains & b.work_domains) and not announced:
        score -= 0.20
        con.append(f"work emails at different companies ({', '.join(sorted(a.work_domains | b.work_domains))})")
    if a.card_org and b.card_org and _fold(a.card_org) != _fold(b.card_org):
        score -= 0.30
        con.append(f"different employers on their cards ({a.card_org} vs {b.card_org})")
    # How common is this name among the contacts? A unique full name seen in two apps is
    # almost certainly one person; a name shared by several contacts needs more proof.
    key = full_name_key(a.tokens) or full_name_key(b.tokens)
    lookalikes = first_counts.get(key, 0) if key else 0
    if key and lookalikes <= 2 and len(a.tokens) > 1 and len(b.tokens) > 1 and not (a.is_bare_handle or b.is_bare_handle):
        score += 0.05
        pro.append("no other contact has a name like this")
    elif lookalikes > 2:
        for x, y in ((a, b), (b, a)):
            work_only = x.work_domains and not x.phones and x.channels <= {"gmail", "contacts"}
            social_only = bool(y.channels) and y.channels <= {"messenger", "imessage"} and not y.work_domains
            if work_only and social_only:
                score -= 0.15
                con.append(f"{lookalikes} contacts share this name, and {x.name} is a work-only email contact "
                           f"while {y.name} only chats socially")
                break
    return Judgement(a=a.id, b=b.id, score=round(max(0.0, min(1.0, score)), 3), reasons_for=pro, reasons_against=con)


# --------------------------------------------------------------------------- LLM tie-breaker
class _SamePerson(BaseModel):
    same_person: bool
    confidence: float = Field(ge=0, le=1)
    reason: str = ""


def _ask_llm(a: _View, b: _View, j: Judgement, settings: Settings) -> _SamePerson | None:
    from recall.llm.generator import generate_json, use_sample_provider

    if use_sample_provider(settings):
        return None
    payload = {
        "contact_a": {"name": a.name, "emails": sorted(a.emails), "phones": sorted(a.phones), "channels": sorted(a.channels),
                      "card_org": a.card_org, "recent_messages_from_them": a.inbound_text[-8:]},
        "contact_b": {"name": b.name, "emails": sorted(b.emails), "phones": sorted(b.phones), "channels": sorted(b.channels),
                      "card_org": b.card_org, "recent_messages_from_them": b.inbound_text[-8:]},
        "signals_for": j.reasons_for, "signals_against": j.reasons_against,
    }
    system = ("You decide whether two entries in one person's contacts are the same human. Same or similar names are "
              "common, so require concrete evidence (shared topics, handles, places, people). Answer same_person, "
              "confidence 0-1, and one sentence of reason.")
    try:
        import asyncio
        import concurrent.futures

        async def _run() -> _SamePerson:
            out = await generate_json(json.dumps(payload, ensure_ascii=False), system, _SamePerson, role="critic")
            assert isinstance(out, _SamePerson)
            return out

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(lambda: asyncio.run(_run())).result(timeout=25)
    except Exception as exc:  # noqa: BLE001 — never break ingest on the tie-breaker
        logger.info("name-merge LLM fallback: %s", exc)
        return None


# --------------------------------------------------------------------------- applying merges
_DDL = """
CREATE TABLE IF NOT EXISTS merge_decisions (
    id TEXT PRIMARY KEY,
    entity_a TEXT NOT NULL,
    entity_b TEXT NOT NULL,
    name_a TEXT NOT NULL,
    name_b TEXT NOT NULL,
    score REAL NOT NULL,
    confidence REAL NOT NULL DEFAULT 1.0,
    decision TEXT NOT NULL,
    reasons_for TEXT NOT NULL DEFAULT '[]',
    reasons_against TEXT NOT NULL DEFAULT '[]',
    merged_into TEXT NOT NULL DEFAULT '',
    run_id TEXT NOT NULL DEFAULT '',
    decided_at TEXT NOT NULL DEFAULT ''
);
"""


def _merge_entities(store: WorldStore, winner: str, loser: str) -> None:
    c = store._conn  # noqa: SLF001
    w = c.execute("SELECT * FROM entities WHERE id = ?", (winner,)).fetchone()
    lo = c.execute("SELECT * FROM entities WHERE id = ?", (loser,)).fetchone()
    if w is None or lo is None:
        return
    aliases = json.loads(w["aliases_json"] or "[]")
    for alias in json.loads(lo["aliases_json"] or "[]") + [lo["canonical_name"]]:
        if alias not in aliases:
            aliases.append(alias)
    attrs = json.loads(w["attributes_json"] or "{}")
    for k, v in json.loads(lo["attributes_json"] or "{}").items():
        attrs.setdefault(k, v)
    history = json.loads(w["attribute_history_json"] or "[]") + json.loads(lo["attribute_history_json"] or "[]")
    first = min(x for x in (w["first_seen_at"], lo["first_seen_at"]) if x) if (w["first_seen_at"] or lo["first_seen_at"]) else ""
    last = max(w["last_seen_at"] or "", lo["last_seen_at"] or "")
    c.execute(
        "UPDATE entities SET aliases_json=?, attributes_json=?, attribute_history_json=?, first_seen_at=?, last_seen_at=?, "
        "mention_count=mention_count+? WHERE id=?",
        (json.dumps(aliases, ensure_ascii=False), json.dumps(attrs, ensure_ascii=False), json.dumps(history, ensure_ascii=False),
         first, last, int(lo["mention_count"] or 0), winner),
    )
    for table, col in (("observations", "entity_id"), ("conflicts", "entity_id"), ("employment_assumptions", "entity_id"),
                       ("relationship_assessments", "entity_id"), ("relationships", "from_id"), ("relationships", "to_id"),
                       ("identity_assumptions", "candidate_entity_id"), ("identity_questions", "candidate_a"),
                       ("identity_questions", "candidate_b")):
        c.execute(f"UPDATE {table} SET {col} = ? WHERE {col} = ?", (winner, loser))  # noqa: S608 — fixed identifiers
    # handle_links ids are hash(entity_id, handle) — re-key them so later upserts don't collide
    from recall.engines.world.identity_service import _stable_id

    for link in c.execute("SELECT * FROM handle_links WHERE entity_id = ?", (loser,)).fetchall():
        row = dict(link)
        row["id"] = f"hl_{_stable_id(winner, row['handle_value'])}"
        row["entity_id"] = winner
        c.execute(
            "INSERT OR IGNORE INTO handle_links (id, entity_id, handle_kind, handle_value, confidence, status, source_ref, created_at) "
            "VALUES (:id, :entity_id, :handle_kind, :handle_value, :confidence, :status, :source_ref, :created_at)", row)
    c.execute("DELETE FROM handle_links WHERE entity_id = ?", (loser,))
    c.execute("UPDATE OR IGNORE preference_profiles SET entity_id = ? WHERE entity_id = ?", (winner, loser))
    c.execute("DELETE FROM preference_profiles WHERE entity_id = ?", (loser,))
    c.execute("DELETE FROM entities WHERE id = ?", (loser,))
    c.commit()


def _retype_services(store: WorldStore, views: dict[str, _View]) -> list[str]:
    c = store._conn  # noqa: SLF001
    moved = []
    for v in list(views.values()):
        locals_ = {e.split("@", 1)[0] for e in v.emails}
        words = set(_fold(v.name).split())
        if v.phones:
            continue
        if (v.emails and locals_ <= _SERVICE_LOCALS) or (words & _SERVICE_WORDS and not v.phones):
            c.execute("UPDATE entities SET type = 'org' WHERE id = ?", (v.id,))
            moved.append(v.name)
            views.pop(v.id)
    c.commit()
    return moved


# --------------------------------------------------------------------------- entry point
def run_name_merge(
    *,
    store: WorldStore,
    settings: Settings,
    interactions: list[Any],
    tracer: Tracer | None = None,
    run_id: str = "",
) -> dict[str, int]:
    """Merge duplicate people; return counts. Safe to run repeatedly."""
    c = store._conn  # noqa: SLF001
    c.executescript(_DDL)
    if "confidence" not in {r[1] for r in c.execute("PRAGMA table_info(merge_decisions)")}:
        c.execute("ALTER TABLE merge_decisions ADD COLUMN confidence REAL NOT NULL DEFAULT 1.0")
    views = _build_views(store, interactions)
    not_people = _retype_services(store, views)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    log_path = Path(settings.logs_dir) / (run_id or "world-offline") / "merge_decisions.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    parent: dict[str, str] = {eid: eid for eid in views}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def winner_of(x: str, y: str) -> tuple[str, str]:
        vx, vy = views[x], views[y]
        key = lambda v: (not v.is_bare_handle, len(v.tokens) > 1, len(v.emails) + len(v.phones), v.mentions)  # noqa: E731
        return (x, y) if key(vx) >= key(vy) else (y, x)

    decisions: list[dict[str, Any]] = []

    def record(j: Judgement, decision: str, merged_into: str = "", confidence: float = 1.0) -> None:
        row = {"id": f"md_{j.a}_{j.b}", "entity_a": j.a, "entity_b": j.b, "name_a": views[j.a].name, "name_b": views[j.b].name,
               "score": j.score, "confidence": confidence, "decision": decision, "reasons_for": j.reasons_for,
               "reasons_against": j.reasons_against, "merged_into": merged_into, "run_id": run_id, "decided_at": now}
        decisions.append(row)
        c.execute(
            "INSERT OR REPLACE INTO merge_decisions (id, entity_a, entity_b, name_a, name_b, score, confidence, decision, "
            "reasons_for, reasons_against, merged_into, run_id, decided_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (row["id"], j.a, j.b, row["name_a"], row["name_b"], j.score, confidence, decision,
             json.dumps(j.reasons_for, ensure_ascii=False), json.dumps(j.reasons_against, ensure_ascii=False),
             merged_into, run_id, now),
        )


    # 1) identical handles -> always merge
    by_handle: dict[str, list[str]] = {}
    for v in views.values():
        for h in v.emails | v.phones:
            by_handle.setdefault(h, []).append(v.id)
    for h, ids in by_handle.items():
        for other in ids[1:]:
            ra, rb = find(ids[0]), find(other)
            if ra != rb:
                j = Judgement(ids[0], other, 1.0, [f"both have {h}"], [])
                w, lo = winner_of(ra, rb)
                parent[lo] = w
                record(j, "merged", w)

    # 2) similar names -> score, then decide
    first_counts: dict[str, int] = {}
    roots = sorted({find(x) for x in views})
    for eid in list(views):  # fold merged-in handles/channels/text into each root's view
        r = find(eid)
        if r != eid:
            views[r].emails |= views[eid].emails
            views[r].phones |= views[eid].phones
            views[r].channels |= views[eid].channels
            views[r].inbound_text += views[eid].inbound_text
            views[r].mentions += views[eid].mentions
    for r in roots:
        v = views[r]
        if v.first and not v.is_bare_handle:
            first_counts[v.first] = first_counts.get(v.first, 0) + 1
            fk = full_name_key(v.tokens)
            if fk:
                first_counts[fk] = first_counts.get(fk, 0) + 1
            if len(v.last) > 1:  # count full surnames matched by last initial
                key = f"{v.first} {v.last[0]}"
                first_counts[key] = first_counts.get(key, 0) + 1
    ids = roots
    topics = TopicModel({r: views[r] for r in roots})
    judged: list[Judgement] = []
    for i, x in enumerate(ids):
        for y in ids[i + 1:]:
            if find(x) == find(y):
                continue
            j = judge(views[x], views[y], first_counts, topics)
            if j is not None:
                judged.append(j)

    # Decide, strongest pairs first. A pair is merged only when it is strong AND
    # unambiguous; when another contact is about as good a match, or the evidence
    # is middling, Recall asks the user (an LLM may settle it first in live mode).
    pair_score = {frozenset((j.a, j.b)): j.score for j in judged}
    cands: dict[str, list[Judgement]] = {}
    for j in judged:
        cands.setdefault(j.a, []).append(j)
        cands.setdefault(j.b, []).append(j)

    def members(root: str) -> list[str]:
        return [e for e in views if find(e) == root]

    def conflicts(ra: str, rb: str) -> bool:
        """Someone in one cluster is clearly NOT someone in the other (e.g. different employers)."""
        return any(pair_score.get(frozenset((x, y)), 1.0) < CANNOT_LINK for x in members(ra) for y in members(rb))

    def rival_for(side: str, partner: str, j: Judgement) -> str | None:
        """Another candidate about as good as this one, who isn't the same person as the partner."""
        for k in cands[side]:
            if k is j or find(k.a) == find(k.b) or k.score < ASK_AT or j.score - k.score >= AMBIGUITY_MARGIN:
                continue
            rival = k.b if k.a == side else k.a
            if find(rival) != find(partner) and pair_score.get(frozenset((rival, partner)), 0.0) < ASK_AT:
                return rival
        return None

    merged = asked = 0
    for j in sorted(judged, key=lambda x: -x.score):
        ra, rb = find(j.a), find(j.b)
        if ra == rb:
            continue
        confidence = round(1 / (1 + math.exp(-(j.score - SEPARATE_SCORE) / TEMPERATURE)), 3)
        if conflicts(ra, rb):
            j.reasons_against.append("one of them is already matched to someone the other clearly isn't")
            record(j, "kept_apart", "", confidence)
            continue
        rival = rival_for(j.a, j.b, j) or rival_for(j.b, j.a, j)
        decision = "kept_apart"
        if j.score >= MERGE_AT and not rival:
            decision = "merged"
        elif j.score >= ASK_AT or rival:
            verdict = _ask_llm(views[j.a], views[j.b], j, settings)
            if verdict is not None and verdict.confidence >= 0.8:
                (j.reasons_for if verdict.same_person else j.reasons_against).append(f"LLM: {verdict.reason}")
                decision = "merged" if verdict.same_person else "kept_apart"
            else:
                decision = "ask"
                if rival:
                    j.reasons_against.append(f"'{views[rival].name}' is an equally good match — asking instead of guessing")
        if decision == "merged":
            w, lo = winner_of(ra, rb)
            parent[lo] = w
            record(j, "merged", w, confidence)
            merged += 1
        elif decision == "ask":
            record(j, "ask", "", confidence)
            asked += 1
            store.enqueue_identity_question({
                "id": f"iq_merge_{j.a}_{j.b}", "handle_kind": "entity", "handle_value": views[j.a].name,
                "candidate_a": j.a, "candidate_b": j.b,
                "question": f"Are '{views[j.a].name}' and '{views[j.b].name}' the same person?",
                "evidence_hash": f"merge:{j.a}:{j.b}", "created_at": now,
            })
        else:
            record(j, "kept_apart", "", confidence)
    c.commit()

    # apply merges (losers fold into their root)
    applied = 0
    for eid in list(views):
        root = find(eid)
        if root != eid:
            _merge_entities(store, root, eid)
            applied += 1
    c.commit()

    with log_path.open("a", encoding="utf-8") as fh:
        for d in decisions:
            fh.write(json.dumps(d, ensure_ascii=False) + "\n")
    counts = {"merged": applied, "asked": asked, "kept_apart": sum(1 for d in decisions if d["decision"] == "kept_apart"),
              "not_people": len(not_people)}
    if tracer is not None:
        tracer.event("world", "name_merge", counts)
    return counts
