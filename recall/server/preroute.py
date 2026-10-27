"""Before the agent sees a question: handle the kinds it can't route on its own.

The agent briefs one *named* person. People also ask in other ways:

  role instead of a name     "what do I owe my landlord?", "anything to answer my mom about?"
  company / meeting name     "prep me for the Énergie Boréale call"
  a date instead of a name   "who am I meeting on November 6?"
  a name plus a hint         "Omar from my MBA", "Daniel Kim on Thursday"
  several people at once     "what's on my calendar Friday?", "whose birthday is coming up?",
                             "what do I owe people this week?"
  a yes/no about the diary   "am I having lunch with Priya on the 21st?"

``plan(text)`` returns either a finished answer built straight from the calendar and
messages, or the person to brief (plus extra cited points to put on top of the
agent's brief). It only uses what's in the user's own data — contact cards, how
the user addresses people, calendar invites, who asked what — never a fixed list
of names. When nothing applies it returns an empty plan and the agent runs as before.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import unicodedata
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from recall.config import get_settings
from recall.schemas import Brief, Evidence, RunState, TalkingPoint, Utterance

TZ = ZoneInfo("America/Toronto")
OPEN_LOOP_DAYS = 21

ROLES: dict[str, list[str]] = {
    "mom": ["mom", "mum", "mother", "mama", "maman", "mère", "mere"],
    "dad": ["dad", "father", "papa", "père", "pere"],
    "sister": ["sister", "sis", "sœur", "soeur"],
    "brother": ["brother", "frère", "frere"],
    "landlord": ["landlord", "landlady", "proprio", "propriétaire", "proprietaire"],
    "boss": ["boss", "manager", "patron"],
    "professor": ["prof", "professor", "professeur", "professeure", "supervisor"],
    "cofounder": ["cofounder", "co-founder", "cofondateur", "cofondatrice"],
    "roommate": ["roommate", "roomie", "coloc", "colocataire"],
    "recruiter": ["recruiter", "recruiting", "recruteur"],
    "investor": ["investor", "vc", "investisseur"],
    "doctor": ["doctor", "doc", "médecin", "medecin"],
}
REQUEST = re.compile(
    r"\b(can you|could you|would you|can u|please|pls|send me|let me know|lmk|confirm|need you to|"
    r"pourriez[- ]vous|pouvez[- ]vous|peux[- ]tu|pourrais[- ]tu|merci de|envoie[- ]moi|confirmer)\b",
    re.I,
)
PROMISE = re.compile(
    r"\b(i'?ll|i will|will) (send|get back|share|confirm|follow up|forward)|je vous envoie|je t'envoie|je te reviens|je vous reviens",
    re.I,
)
DONE = re.compile(r"\b(attached|here(?:'s| is| are| you go)|just sent|sent it|shared it|voici|ci-joint|envoyé|done ✅)\b", re.I)
OWE = re.compile(
    r"\b(owe|open loops?|waiting on me|need to (answer|reply|respond|get back|send|confirm)|"
    r"have to (answer|reply|send)|haven'?t (answered|replied)|to answer .* about|answer .* about)\b",
    re.I,
)
CALENDAR = re.compile(r"\b(calendar|schedule|agenda|what do i have|what'?s on)\b", re.I)
BIRTHDAY = re.compile(r"\b(birthdays?|bday|anniversaire)\b", re.I)
BDAY_MARK = re.compile(r"(🎂|birthday|bday|anniversaire|\bfête\b|\bfete\b)", re.I)
WHO_MEET = re.compile(r"\bwho (am i|are we) (meeting|seeing)|\bwho('?s| is) (on|in) my\b", re.I)
YESNO_EVENT = re.compile(r"^\s*(am i|are we|do i have|is there|did i have)\b.*\b(lunch|dinner|breakfast|brunch|coffee|drinks?|meeting|call)\b", re.I)
MEAL = re.compile(r"\b(lunch|dinner|breakfast|brunch|coffee|drinks?|meeting|call)\b", re.I)
ASSISTANT_TITLE = re.compile(r"\b(assistant|coordinator|admin|ea)\b", re.I)
STOP = set(
    "a an the i me my we our you your he she they them it is am are was were be been do does did have has had "
    "what whats who whom whose when where why how which anything something any some to of in on at for from with "
    "about and or but not no yes up out into this that these those there here now soon today tomorrow tonight week "
    "next last coming upcoming meeting meet seeing see brief prep catch call sync chat lunch dinner coffee need "
    "should know tell new going get got let lets please can could would will ever reply replied answer owe people "
    "calendar schedule agenda birthday day days".split()
)
COMMON_WORDS = {"will", "may", "mark", "bill", "art", "sun", "joy", "hope", "faith", "chase", "rich", "hunter"}
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MONTHS = {m: i for i, ms in enumerate(
    [["jan", "january", "janvier"], ["feb", "february", "février", "fevrier"], ["mar", "march", "mars"],
     ["apr", "april", "avril"], ["may", "mai"], ["jun", "june", "juin"], ["jul", "july", "juillet"],
     ["aug", "august", "août", "aout"], ["sep", "sept", "september", "septembre"], ["oct", "october", "octobre"],
     ["nov", "november", "novembre"], ["dec", "december", "décembre", "decembre"]], 1) for m in ms}


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


def words(s: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", norm(s))


# --------------------------------------------------------------------------- world snapshot
@dataclass
class Person:
    id: str
    name: str
    names: list[str]
    handles: set[str]
    about: str  # card fields, normalised
    mentions: int
    label: str


@dataclass
class Msg:
    id: str
    source: str
    handle: str
    direction: str
    ts: datetime
    subject: str
    text: str
    ref: str


@dataclass
class Event:
    id: str
    title: str
    start: datetime
    end: datetime
    location: str
    attendees: list[str]
    ref: str


@dataclass
class World:
    now: datetime
    people: list[Person]
    by_handle: dict[str, Person]
    msgs: list[Msg]
    events: list[Event]
    owner_handles: set[str] = field(default_factory=set)

    def person_of(self, handle: str) -> Person | None:
        return self.by_handle.get(handle.lower())


_cache: tuple[float, str, World] | None = None


def load_world(settings: Any = None) -> World | None:
    """Contact cards from the world DB + recent messages and events from the query layer (cached 60 s)."""
    global _cache
    from recall.data.queries import all_events_in_window, all_interactions, resolve_now
    from recall.data.query_layer import get_query_layer
    from recall.engines.world.store import WorldStore, world_db_path

    settings = settings or get_settings()
    path = world_db_path(settings)
    if not path.exists():
        return None
    key = f"{path}:{path.stat().st_mtime}"
    if _cache and _cache[1] == key and time.monotonic() - _cache[0] < 60:
        return _cache[2]
    now = resolve_now(settings)
    layer = get_query_layer(settings)  # fresh: the process-wide layer caches results forever
    store = WorldStore(path=path)
    conn = store._conn  # noqa: SLF001
    labels = {r[0]: r[1] for r in conn.execute(
        "SELECT entity_id, label FROM relationship_assessments ORDER BY assessed_at")} if conn.execute(
        "SELECT name FROM sqlite_master WHERE name = 'relationship_assessments'").fetchone() else {}
    people: list[Person] = []
    for eid, name, aliases_json, attrs_json, mentions in conn.execute(
            "SELECT id, canonical_name, aliases_json, attributes_json, mention_count FROM entities WHERE type = 'person'"):
        aliases = json.loads(aliases_json or "[]")
        attrs = json.loads(attrs_json or "{}")
        handles = {a.lower() for a in aliases if "@" in a or a.startswith("+")}
        handles |= {"+" + a.split("@")[0] for a in aliases if a.endswith("@s.whatsapp.net")}
        names = [a for a in [name, *aliases] if "@" not in a and not a.startswith("+")]
        about = norm(" ".join([*names, *(str(attrs.get(k, "")) for k in ("org", "title", "city", "notes")), labels.get(eid, "")]))
        people.append(Person(eid, name, names, handles, about, int(mentions or 0), labels.get(eid, "")))
    by_handle = {h: p for p in people for h in p.handles}
    msgs = [
        Msg(r.interaction_id, r.source, r.handle_value.lower(), r.direction, _ts(r.ts_utc) or now, r.subject or "", r.text or "", r.source_ref)
        for r in all_interactions(source_tag="preroute", since_days=400, limit=20000, now=now, settings=settings, layer=layer)
    ]
    events = []
    for e in all_events_in_window(source_tag="preroute", days_back=60, days_forward=60, now=now, settings=settings, layer=layer):
        start, end = _ts(e.start_utc), _ts(e.end_utc)
        if start:
            events.append(Event(e.event_id, e.title, start, end or start, e.location, [a.lower() for a in e.attendees], e.source_ref))
    world = World(now, people, by_handle, msgs, events)
    invites = [e for e in events if e.attendees]
    top = Counter(a for e in invites for a in set(e.attendees)).most_common(1)
    if top and len(invites) >= 3 and top[0][1] >= 0.6 * len(invites):
        owner = world.person_of(top[0][0])
        world.owner_handles = owner.handles if owner else {top[0][0]}
        if owner:
            world.people = [p for p in people if p.id != owner.id]
    _cache = (time.monotonic(), key, world)
    return world


def _ts(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# --------------------------------------------------------------------------- reading the question
def find_dates(text: str, now: datetime) -> list[date]:
    """Dates the question points at (local time): today/tomorrow/weekday/'Nov 6'/'the 21st'/'this week'."""
    low = norm(text)
    today = now.astimezone(TZ).date()
    out: list[date] = []
    if re.search(r"\btoday\b|\btonight\b", low):
        out.append(today)
    if re.search(r"\btomorrow\b", low):
        out.append(today + timedelta(days=1))
    for i, wd in enumerate(WEEKDAYS):
        if re.search(rf"\b{wd}\b", low):
            out.append(today + timedelta(days=(i - today.weekday()) % 7))
    for m in re.finditer(r"\b([a-z]{3,9})\.? (\d{1,2})(?:st|nd|rd|th)?\b|\b(\d{1,2})(?:st|nd|rd|th)? ([a-z]{3,9})\b", low):
        mon, day = (m.group(1), m.group(2)) if m.group(1) else (m.group(4), m.group(3))
        if mon in MONTHS:
            out.append(_nearest(today, MONTHS[mon], int(day)))
    for m in re.finditer(r"\bthe (\d{1,2})(?:st|nd|rd|th)\b", low):
        d = int(m.group(1))
        cands = []
        for k in (-1, 0, 1):
            y, mo = today.year, today.month + k
            y, mo = (y - 1, 12) if mo == 0 else (y + 1, 1) if mo == 13 else (y, mo)
            try:
                cands.append(date(y, mo, d))
            except ValueError:
                pass
        if cands:
            out.append(min(cands, key=lambda c: abs((c - today).days)))
    return sorted(set(out))


def _nearest(today: date, month: int, day: int) -> date:
    cands = []
    for y in (today.year - 1, today.year, today.year + 1):
        try:
            cands.append(date(y, month, day))
        except ValueError:
            pass
    future = [c for c in cands if c >= today - timedelta(days=14)]
    return min(future or cands, key=lambda c: abs((c - today).days))


def week_range(text: str, now: datetime) -> tuple[date, date] | None:
    low = norm(text)
    today = now.astimezone(TZ).date()
    if re.search(r"\bthis week\b", low):
        return today, today + timedelta(days=6 - today.weekday())
    if re.search(r"\bnext week\b", low):
        start = today + timedelta(days=7 - today.weekday())
        return start, start + timedelta(days=6)
    return None


def find_people(text: str, world: World) -> list[Person]:
    """People named in the question: full names first, then unambiguous first names / aliases."""
    low = " " + " ".join(words(text)) + " "
    hits: dict[str, tuple[int, Person]] = {}
    for p in world.people:
        best = 0
        for n in p.names:
            clean = " ".join(words(re.sub(r"\(.*?\)", "", n)))
            if not clean:
                continue
            toks = clean.split()
            if len(toks) > 1 and f" {clean} " in low:
                best = max(best, 2)
            elif toks[0] not in COMMON_WORDS and toks[0] not in STOP and len(toks[0]) >= 3 and f" {toks[0]} " in low:
                best = max(best, 1)
        if best:
            hits[p.id] = (best, p)
    if any(score == 2 for score, _ in hits.values()):
        return [p for score, p in hits.values() if score == 2]
    return [p for _, p in hits.values()]


def find_role(text: str) -> str | None:
    low = norm(text)
    for role, syns in ROLES.items():
        for s in syns:
            if re.search(rf"\b(my|ma|mon|our) {re.escape(norm(s))}\b", low):
                return role
    return None


def person_for_role(role: str, world: World) -> tuple[Person | None, str]:
    """Who plays this role, from the card (title/name/label) and how the user addresses them."""
    syns = [norm(s) for s in ROLES[role]]
    score: Counter[str] = Counter()
    for p in world.people:
        if any(re.search(rf"\b{re.escape(s)}\b", p.about) for s in syns):
            score[p.id] += 3
    pat = re.compile(r"\b(" + "|".join(re.escape(s) for s in syns) + r")\b")
    for m in world.msgs:
        if m.direction == "out" and pat.search(norm(m.text)):
            p = world.person_of(m.handle)
            if p:
                score[p.id] += 1
    ranked = score.most_common(2)
    if not ranked:
        return None, ""
    # Clear winner only: the person you most often call "mom", well ahead of anyone else.
    if len(ranked) == 2 and (ranked[0][1] < 1.5 * ranked[1][1] or ranked[0][1] - ranked[1][1] < 2):
        return None, ""
    p = next(x for x in world.people if x.id == ranked[0][0])
    return p, f"your {role}"


# --------------------------------------------------------------------------- plan
@dataclass
class Plan:
    answer: RunState | None = None
    entity_id: str | None = None
    extra: list[tuple[TalkingPoint, Evidence]] = field(default_factory=list)
    why: str = ""


def plan(text: str, settings: Any = None) -> Plan:
    world = load_world(settings)
    if world is None or not text.strip():
        return Plan()
    named = find_people(text, world)
    role = find_role(text)
    days = find_dates(text, world.now)
    owe = bool(OWE.search(text))

    # Yes/no about a specific date with a named person: answer from the calendar + their messages.
    if named and days and YESNO_EVENT.search(text):
        return _event_check(text, named, days[0], world)

    if not named and not role:
        if BIRTHDAY.search(text):
            return _birthdays(text, world)
        if owe:
            return _open_loops_answer(text, world, None)
        if days and WHO_MEET.search(text):
            return _who_on(text, days[0], world)
        if CALENDAR.search(text) and (days or week_range(text, world.now)):
            rng = week_range(text, world.now)
            return _calendar(text, days[0] if days else rng[0], days[0] if days else rng[1], world)
        ev = _event_named(text, world)
        if ev:
            who = _primary_attendee(ev, world)
            if who:
                return Plan(entity_id=who.id, extra=[_event_point(ev, world)], why=f"calendar: {ev.title}")

    target: Person | None = None
    why = ""
    if role and not named:
        target, why = person_for_role(role, world)
    elif len(named) > 1:
        target, why = _disambiguate(text, named, days, world)
    if target is None:
        return Plan()
    extra = _open_loops(world, target) if owe else []
    for d in days:
        for ev in _events_on(d, d, world):
            if target.handles & set(ev.attendees) or _name_in(target, ev.title):
                extra.insert(0, _event_point(ev, world))
    return Plan(entity_id=target.id, extra=extra, why=why)


def _disambiguate(text: str, named: list[Person], days: list[date], world: World) -> tuple[Person | None, str]:
    """Same name, several people: use the date (who's on the calendar) or the other words (MBA, UofT…)."""
    score: dict[str, float] = defaultdict(float)
    for d in days:
        for ev in _events_on(d, d, world):
            for p in named:
                if p.handles & set(ev.attendees) or _name_in(p, ev.title):
                    score[p.id] += 2
    name_toks = {t for p in named for n in p.names for t in words(re.sub(r"\(.*?\)", "", n))}
    hints = [w for w in words(text) if w not in STOP and w not in name_toks and len(w) >= 2 and w not in WEEKDAYS and w not in MONTHS]
    for p in named:
        for h in hints:
            if re.search(rf"\b{re.escape(h)}\b", p.about):
                score[p.id] += 1
    ranked = sorted(score.items(), key=lambda kv: -kv[1])
    if not ranked or (len(ranked) > 1 and ranked[0][1] == ranked[1][1]):
        return None, ""
    p = next(x for x in named if x.id == ranked[0][0])
    return p, "matched by " + ("calendar" if days else "context")


# --------------------------------------------------------------------------- calendar helpers
def _events_on(first: date, last: date, world: World) -> list[Event]:
    return sorted(
        (e for e in world.events if first <= local_day(e) <= last and "cancel" not in e.title.lower()),
        key=lambda e: e.start,
    )


def _name_in(p: Person, title: str) -> bool:
    low = " " + " ".join(words(title)) + " "
    for n in p.names:
        toks = words(re.sub(r"\(.*?\)", "", n))
        if toks and (f" {' '.join(toks)} " in low or (len(toks[0]) >= 3 and f" {toks[0]} " in low)):
            return True
    return False


def _attendees(ev: Event, world: World) -> list[Person]:
    seen: dict[str, Person] = {}
    for a in ev.attendees:
        p = world.person_of(a)
        if p and a not in world.owner_handles:
            seen[p.id] = p
    if not seen:
        firsts = Counter(words(re.sub(r"\(.*?\)", "", n))[0] for p in world.people for n in p.names[:1] if words(n))
        for p in world.people:
            first = (words(re.sub(r"\(.*?\)", "", p.name)) or [""])[0]
            if _name_in(p, ev.title) and (firsts[first] == 1 or len(words(p.name)) > 1 and " ".join(words(p.name)) in norm(ev.title)):
                seen[p.id] = p
    return list(seen.values())


def _primary_attendee(ev: Event, world: World) -> Person | None:
    """The person this meeting is really about: not an assistant, not someone on every invite."""
    people = _attendees(ev, world)
    if not people:
        return None
    load = Counter(p.id for e in world.events for p in _attendees(e, world)) if len(people) > 1 else Counter()

    def rank(p: Person) -> tuple:
        return (bool(ASSISTANT_TITLE.search(p.about)), load[p.id], -p.mentions)

    return sorted(people, key=rank)[0]


def _event_named(text: str, world: World) -> Event | None:
    """'Prep me for the Énergie Boréale call' → the upcoming event whose distinctive title words are in the question."""
    upcoming = [e for e in world.events if e.end >= world.now - timedelta(hours=2)]
    df = Counter(w for e in upcoming for w in set(words(e.title)))
    q = set(words(text))
    best, best_score = None, 0.0
    for e in upcoming:
        toks = [w for w in set(words(e.title)) if len(w) >= 4 and w not in STOP and df[w] <= 2]
        hit = sum(1.0 / df[w] for w in toks if w in q)
        if hit > best_score:
            best, best_score = e, hit
    return best if best_score >= 1.0 else None


def all_day(ev: Event) -> bool:
    return ev.start.hour == 0 and ev.start.minute == 0 and (ev.end - ev.start) >= timedelta(hours=20)


def local_day(ev: Event) -> date:
    """All-day events are stored at midnight UTC of their date; timed ones are shown in local time."""
    return ev.start.date() if all_day(ev) else ev.start.astimezone(TZ).date()


def when(ev: Event) -> str:
    start = ev.start.astimezone(TZ)
    d = local_day(ev)
    day = d.strftime("%a, %b ") + str(d.day)
    if all_day(ev):
        return day
    end = ev.end.astimezone(TZ)
    return f"{day} at {_clock(start)}" + (f"–{_clock(end)}" if end > start else "")


def _clock(dt: datetime) -> str:
    h = dt.hour % 12 or 12
    return f"{h}:{dt.minute:02d} {'AM' if dt.hour < 12 else 'PM'}"


def _eid(prefix: str, *parts: str) -> str:
    return f"{prefix}_{hashlib.sha1('|'.join(parts).encode()).hexdigest()[:10]}"


def _event_ev(ev: Event) -> Evidence:
    text = f"{ev.title} — {when(ev)}" + (f" · {ev.location}" if ev.location else "")
    return Evidence(id=_eid("cal", ev.id), source="calendar", source_ref=ev.ref or f"calendar:{ev.id}", ts=ev.start, text=text)


def _event_point(ev: Event, world: World, kind: str = "logistics") -> tuple[TalkingPoint, Evidence]:
    people = _attendees(ev, world)
    who = f" with {', '.join(p.name for p in people)}" if people else ""
    where = f" ({ev.location})" if ev.location else ""
    e = _event_ev(ev)
    return TalkingPoint(text=f"{ev.title} — {when(ev)}{who}{where}.", evidence_ids=[e.id], kind=kind, priority=1), e


def _msg_ev(m: Msg) -> Evidence:
    src = m.source if m.source in ("gmail", "imessage", "whatsapp", "calendar", "linkedin") else "memory"
    body = (f"{m.subject}: " if m.subject else "") + m.text
    return Evidence(id=_eid("msg", m.id), source=src, source_ref=m.ref or m.id, ts=m.ts, text=body[:600])


# --------------------------------------------------------------------------- answers
def _state(text: str, world: World, headline: str, points: list[tuple[TalkingPoint, Evidence]], *,
           person: str = "you", caveats: list[str] | None = None, entity: Person | None = None) -> RunState:
    pts = []
    evidence: dict[str, Evidence] = {}
    for i, (pt, ev) in enumerate(points, 1):
        pts.append(pt.model_copy(update={"priority": i}))
        evidence[ev.id] = ev
    brief = Brief(person=person, headline=headline, points=pts, caveats=caveats or [])
    return RunState(
        run_id=f"pre_{uuid.uuid4().hex[:10]}",
        utterance=Utterance(text=text, ts=world.now, mode="text"),
        entity_id=entity.id if entity else None,
        entity_name=entity.name if entity else None,
        evidence=evidence,
        drafts=[brief],
        final_brief=brief,
        status="done",
        context={"preroute": True},
    )


def _calendar(text: str, first: date, last: date, world: World) -> Plan:
    evs = _events_on(first, last, world)
    span = _day(first) if first == last else f"{_day(first)} – {_day(last)}"
    if not evs:
        return Plan(answer=_state(text, world, f"Nothing on your calendar for {span}.", []), why="calendar")
    head = f"{span}: {len(evs)} thing{'s' if len(evs) > 1 else ''} on your calendar"
    return Plan(answer=_state(text, world, head, [_event_point(e, world) for e in evs[:8]]), why="calendar")


def _day(d: date) -> str:
    return d.strftime("%A, %b ") + str(d.day)


def _who_on(text: str, d: date, world: World) -> Plan:
    evs = [e for e in _events_on(d, d, world) if _attendees(e, world)]
    if len(evs) == 1:
        who = _primary_attendee(evs[0], world)
        if who:
            return Plan(entity_id=who.id, extra=[_event_point(evs[0], world)], why="calendar")
    return _calendar(text, d, d, world)


def _birthdays(text: str, world: World) -> Plan:
    horizon = world.now + timedelta(days=45)
    marks = sorted((e for e in world.events if world.now - timedelta(days=1) <= e.start <= horizon and BDAY_MARK.search(e.title)),
                   key=lambda x: x.start)
    points: list[tuple[TalkingPoint, Evidence]] = []
    more: list[Evidence] = []
    done: set[str] = set()
    for e in marks:
        if e.id in done:
            continue
        people = _attendees(e, world)
        p = people[0] if people else None
        day0 = local_day(e)
        same = [x for x in marks if x.id != e.id and x.id not in done
                and local_day(x) in {day0, day0 + timedelta(days=1)} and (p is None or _name_in(p, x.title))]
        done |= {x.id for x in same} | {e.id}
        line = f"{p.name + chr(39) + 's birthday' if p else e.title} — {day0.strftime('%a, %b ')}{day0.day}."
        first = _event_ev(e)
        ids = [first.id]
        for x in same:
            line += f" Also: {x.title}, {when(x)}" + (f" at {x.location}" if x.location else "") + "."
            xe = _event_ev(x)
            more.append(xe)
            ids.append(xe.id)
        points.append((TalkingPoint(text=line, evidence_ids=ids, kind="logistics", priority=1), first))
    if not points:
        return Plan(answer=_state(text, world, "No birthdays on your calendar in the next six weeks.", []), why="birthdays")
    state = _state(text, world, "Birthdays coming up", points)
    state.evidence.update({x.id: x for x in more})
    return Plan(answer=state, why="birthdays")


def _open_loops(world: World, only: Person | None = None) -> list[tuple[TalkingPoint, Evidence]]:
    """Things people asked you for (or you promised) recently that you haven't delivered."""
    since = world.now - timedelta(days=OPEN_LOOP_DAYS)
    by_person: dict[str, list[Msg]] = defaultdict(list)
    for m in world.msgs:
        p = world.person_of(m.handle)
        if p and (only is None or p.id == only.id) and m.handle not in world.owner_handles:
            by_person[p.id].append(m)
    people = {p.id: p for p in world.people}
    loops: list[tuple[datetime, TalkingPoint, Evidence]] = []
    for pid, msgs in by_person.items():
        if pid not in people:
            continue
        msgs.sort(key=lambda m: m.ts)
        asks = [m for m in msgs if m.ts >= since and m.ts <= world.now and (
            (m.direction == "in" and REQUEST.search(m.text)) or (m.direction == "out" and PROMISE.search(m.text)))]
        open_asks = [a for a in asks if not any(o.direction == "out" and o.ts > a.ts and DONE.search(o.text) for o in msgs)]
        if not open_asks:
            continue
        first_in = next((a for a in open_asks if a.direction == "in"), open_asks[0])
        promise = next((o for o in msgs if o.direction == "out" and o.ts > first_in.ts and (
            PROMISE.search(o.text) or re.search(r"\bby (mon|tues|wednes|thurs|fri|satur|sun)day\b|\bby (tomorrow|tonight)\b", o.text, re.I))), None)
        nudges = [a for a in open_asks if a.direction == "in" and a.ts > first_in.ts]
        p = people[pid]
        quote = _quote(first_in)
        line = f"{p.name} — {'asked' if first_in.direction == 'in' else 'you promised'} ({_src(first_in)}, {_short(first_in.ts)}): “{quote}”"
        if promise and promise is not first_in:
            line += f" You said: “{_quote(promise)}”"
        if nudges:
            line += f" They followed up {_short(nudges[-1].ts)}."
        ev = _msg_ev(first_in)
        loops.append((nudges[-1].ts if nudges else first_in.ts, TalkingPoint(text=line, evidence_ids=[ev.id], kind="open_loop", priority=1), ev))
    loops.sort(key=lambda x: x[0], reverse=True)
    return [(pt, ev) for _, pt, ev in loops[:8]]


def _open_loops_answer(text: str, world: World, only: Person | None) -> Plan:
    loops = _open_loops(world, only)
    if not loops:
        return Plan(answer=_state(text, world, "Nothing outstanding that I can see in the last three weeks.", []), why="open loops")
    head = f"You owe {len(loops)} {'person' if len(loops) == 1 else 'people'} a reply or a deliverable"
    return Plan(answer=_state(text, world, head, loops), why="open loops")


def _event_check(text: str, named: list[Person], d: date, world: World) -> Plan:
    p = named[0] if len(named) == 1 else max(named, key=lambda x: x.mentions)
    kind = (MEAL.search(text).group(1) if MEAL.search(text) else "meeting").lower()
    evs = [e for e in _events_on(d, d, world) if p.handles & set(e.attendees) or _name_in(p, e.title)]
    if evs:
        e = evs[0]
        return Plan(answer=_state(text, world, f"Yes — {e.title}, {when(e)}.", [_event_point(e, world)], person=p.name, entity=p), why="event check")
    window = [m for m in world.msgs if world.person_of(m.handle) is p and abs((m.ts.astimezone(TZ).date() - d).days) <= 7
              and re.search(rf"\b{kind}", m.text, re.I)]
    window.sort(key=lambda m: m.ts, reverse=True)
    points = []
    head = f"No {kind} with {p.name} on your calendar for {_day(d)}."
    if window:
        m = window[0]
        if re.search(r"\b(cancel|can'?t make|reschedul|postpone|annul)", m.text, re.I):
            head = f"No — {p.name} cancelled the {kind} for {_day(d)}."
        ev = _msg_ev(m)
        points.append((TalkingPoint(text=f"{'They' if m.direction == 'in' else 'You'} wrote ({_src(m)}, {_short(m.ts)}): “{_quote(m)}”",
                                    evidence_ids=[ev.id], kind="logistics", priority=1), ev))
    return Plan(answer=_state(text, world, head, points, person=p.name, entity=p), why="event check")


def _quote(m: Msg, n: int = 220) -> str:
    t = re.sub(r"\s+", " ", m.text).strip()
    sent = re.split(r"(?<=[.!?])\s+", t)
    keep = [s for s in sent if REQUEST.search(s) or PROMISE.search(s) or re.search(r"\b(by|before|avant)\b", s, re.I)] or sent[:2]
    out = " ".join(dict.fromkeys(keep))
    return out if len(out) <= n else out[: n - 1] + "…"


def _src(m: Msg) -> str:
    return {"gmail": "email", "imessage": "iMessage", "whatsapp": "WhatsApp"}.get(m.source, m.source)


def _short(ts: datetime) -> str:
    t = ts.astimezone(TZ)
    return t.strftime("%b ") + str(t.day)


# --------------------------------------------------------------------------- merging with the agent's brief
def add_points(state: RunState, extra: list[tuple[TalkingPoint, Evidence]]) -> RunState:
    """Put calendar / open-loop points on top of the agent's brief (skipping ones it already said)."""
    if not extra:
        return state
    brief = state.final_brief or (state.drafts[-1] if state.drafts else None)
    if brief is None or state.status != "done":
        return state
    said = norm(" ".join([brief.headline, *(p.text for p in brief.points)]))
    new_pts, ev = [], dict(state.evidence)
    for pt, e in extra:
        key = [w for w in words(pt.text) if len(w) >= 5][:4]
        if key and all(k in said for k in key):
            continue
        new_pts.append(pt)
        ev[e.id] = e
    if not new_pts:
        return state
    points = [*new_pts, *brief.points]
    points = [p.model_copy(update={"priority": i}) for i, p in enumerate(points, 1)]
    new_brief = brief.model_copy(update={"points": points})
    return state.model_copy(update={"final_brief": new_brief, "evidence": ev,
                                    "drafts": [*state.drafts[:-1], new_brief] if state.drafts else [new_brief]})
