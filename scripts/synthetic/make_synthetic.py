"""Generate a large synthetic world in each app's *raw export format*, plus an answer key.

    python scripts/synthetic/make_synthetic.py [--out data/synthetic] [--seed 20261026]

Writes:
    raw/gmail/messages.jsonl          Gmail API users.messages.get (format=full), one per line
    raw/gmail/profile.json            Gmail API users.getProfile
    raw/calendar/events.json          Calendar API events.list (singleEvents=true, showDeleted=true)
    raw/imessage/chat.db              macOS Messages database (subset of the real schema)
    raw/messenger/inbox/*/message_1.json   Facebook "Download your information" export
    raw/contacts/contacts.vcf         vCard 3.0 address book export
    ground_truth/people.json          who every handle really belongs to + facts
    ground_truth/facts.json           fact id -> statement + the messages that prove it
    ground_truth/messages.jsonl       every generated message with its true UTC time and raw location
    ground_truth/questions.json       eval questions with expected behaviour
    ground_truth/fact_keywords.json   keyword groups for cheap automatic grading

Standard library only. Deterministic for a given seed. Everything is fictional.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
import random
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))

from banks import BANKS, EMAIL_THREADS, NOISE, REL_BANKS, TOPICS  # noqa: E402
from beats import BEATS  # noqa: E402
from questions import FACT_KEYWORDS, QUESTIONS  # noqa: E402
from world import CORE, FACTS, GROUPS, NOW, OWNER, OWNER_TZ, START, Person, build_filler  # noqa: E402

TZ = ZoneInfo(OWNER_TZ)
UTC = timezone.utc
APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=UTC)


def local(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=TZ)


T_NOW = local(NOW)
T_START = local(START)
# Keep chatter out of chats after an unanswered ask, so "unanswered" stays true.
FILLER_END = {"kevin": local("2026-10-09 00:00")}


@dataclass
class Msg:
    key: str
    source: str
    chat: str  # pid for 1:1, group id for groups, thread key for email
    sender: str  # "me" or pid (or noise sender id)
    ts: datetime  # aware, local tz
    text: str
    kind: str = "filler"  # beat | filler | noise | media | deleted | system | tapback
    facts: tuple[str, ...] = ()
    subject: str = ""
    to: tuple[str, ...] = ()
    cc: tuple[str, ...] = ()
    raw_ref: dict = field(default_factory=dict)


# --------------------------------------------------------------------------- helpers
def first_name(p: Person) -> str:
    return p.name.split(" ")[0]


def valid_at(entries: list[tuple[str, str, str]], when: datetime) -> str | None:
    d = when.date().isoformat()
    for value, frm, to in entries:
        if (not frm or d >= frm) and (not to or d <= to):
            return value
    return entries[-1][0] if entries else None


def fmt_phone_display(e164: str) -> str:
    d = e164[2:]
    return f"+1 ({d[0:3]}) {d[3:6]}-{d[6:]}"


def us_time(t: datetime) -> str:
    return f"{(t.hour % 12) or 12}:{t:%M} {'AM' if t.hour < 12 else 'PM'}"


def b64url(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def gmail_id(rng: random.Random) -> str:
    return "".join(rng.choice("0123456789abcdef") for _ in range(16))


def recent_bias_time(rng: random.Random, start: datetime, end: datetime) -> datetime:
    """Random time, denser in the last ~4 months, daytime-weighted."""
    span = (end - start).total_seconds()
    u = rng.random() ** 0.6  # skew toward the end
    t = start + timedelta(seconds=span * u)
    hour = rng.choices(range(24), weights=[1, 1, 0, 0, 0, 0, 1, 3, 5, 6, 6, 6, 7, 6, 6, 6, 6, 7, 8, 8, 8, 7, 5, 3])[0]
    return t.replace(hour=hour, minute=rng.randrange(60), second=rng.randrange(60), microsecond=0)


# --------------------------------------------------------------------------- generation
class World:
    def __init__(self, seed: int, scale: float = 1.0) -> None:
        self.rng = random.Random(seed)
        self.scale = scale
        self.people: dict[str, Person] = {p.pid: p for p in CORE + build_filler()}
        self.people["owner"] = OWNER
        for p in self.people.values():
            if p.phones and (p.channels.get("imessage") or p.imessage):
                p.imessage = True
        for g in GROUPS.values():  # everyone in a Messenger group needs a Facebook name
            for pid in g["members"]:
                self.people[pid].msgr_name = self.people[pid].msgr_name or self.people[pid].name
        self.msgs: list[Msg] = []
        self.n = 0
        self._check_unique_handles()

    def _check_unique_handles(self) -> None:
        seen: dict[str, str] = {}
        for p in self.people.values():
            for v, _, _ in p.emails + p.phones:
                if v in seen and seen[v] != p.pid:
                    raise SystemExit(f"handle collision {v}: {seen[v]} vs {p.pid}")
                seen[v] = p.pid

    def key(self) -> str:
        self.n += 1
        return f"m_{self.n:05d}"

    def add(self, **kw) -> Msg:
        m = Msg(key=self.key(), **kw)
        self.msgs.append(m)
        return m

    # ---- beats
    def add_beats(self) -> None:
        for b in BEATS:
            self.add(source=b.source, chat=b.chat if b.source != "gmail" else (b.thread or b.chat),
                     sender=b.sender, ts=local(b.when), text=b.text, kind="beat", facts=b.facts,
                     subject=b.subject, to=b.to, cc=b.cc)
            if b.source == "gmail":
                self.msgs[-1].raw_ref["person"] = b.chat

    # ---- filler chat sessions
    def _bank_for(self, p: Person) -> list:
        names = REL_BANKS.get(p.rel, ["casual_en"])
        name = self.rng.choice(names)
        if p.lang == "fr" and name in ("casual_en",):
            name = "casual_fr"
        if p.rel == "family":
            name = "family"
        return BANKS[name]

    def add_filler(self) -> None:
        rng = self.rng
        for p in self.people.values():
            if p.pid == "owner" or not p.filler_sessions:
                continue
            chans = {c: w for c, w in p.channels.items() if c != "gmail"}
            for _ in range(round(p.filler_sessions * self.scale)):
                if not chans:
                    break
                src = rng.choices(list(chans), weights=list(chans.values()))[0]
                t = recent_bias_time(rng, T_START, FILLER_END.get(p.pid, T_NOW - timedelta(hours=2)))
                dialogue = rng.choice(self._bank_for(p))
                a_is_me = rng.random() < 0.5
                for speaker, text in dialogue:
                    sender = "me" if (speaker == "a") == a_is_me else p.pid
                    self.add(source=src, chat=p.pid, sender=sender, ts=t, text=text)
                    t += timedelta(minutes=rng.choice([0, 1, 1, 2, 3, 5, 8, 15, 40]), seconds=rng.randrange(60))
                # Realistic clutter
                r = rng.random()
                if src == "imessage" and r < 0.2:
                    last = self.msgs[-1]
                    self.add(source=src, chat=p.pid, sender="me" if last.sender != "me" else p.pid,
                             ts=t, text=f"Loved “{last.text}”", kind="tapback")
                elif src == "messenger" and r < 0.1:
                    self.add(source=src, chat=p.pid, sender=p.pid, ts=t, text="<photo>", kind="media")
            # Email filler threads
            if p.channels.get("gmail") and p.emails:
                for _ in range(max(1, p.filler_sessions // 3) if p.filler_sessions else 0):
                    subj, body_them, body_me = rng.choice(EMAIL_THREADS)
                    t = recent_bias_time(rng, T_START, T_NOW - timedelta(hours=6))
                    thread = f"t_{p.pid}_{self.n}"
                    fmt = {"me_first": "Jordan", "first": first_name(p), "topic": rng.choice(TOPICS)}
                    m = self.add(source="gmail", chat=thread, sender=p.pid, ts=t, text=body_them.format(**fmt), subject=subj)
                    m.raw_ref["person"] = p.pid
                    if rng.random() < 0.75:
                        m2 = self.add(source="gmail", chat=thread, sender="me", ts=t + timedelta(hours=rng.randint(1, 30)),
                                      text=body_me.format(**fmt), subject="Re: " + subj)
                        m2.raw_ref["person"] = p.pid

    def add_groups(self) -> None:
        rng = self.rng
        for gid, g in GROUPS.items():
            members = g["members"]
            for _ in range(round(g["sessions"] * self.scale)):
                t = recent_bias_time(rng, T_START, T_NOW - timedelta(hours=3))
                bank = BANKS["family"] if gid == "msgr_family" else BANKS["school"] if "study" in gid else BANKS["startup"] if "founders" in gid else BANKS["casual_en"]
                dialogue = rng.choice(bank)
                speakers = {"a": rng.choice(members + ["me"]), "b": rng.choice(members + ["me"])}
                if speakers["a"] == speakers["b"]:
                    speakers["b"] = "me" if speakers["a"] != "me" else members[0]
                for speaker, text in dialogue:
                    self.add(source=g["source"], chat=gid, sender=speakers[speaker], ts=t, text=text)
                    t += timedelta(minutes=rng.choice([0, 1, 2, 4, 9]), seconds=rng.randrange(60))

    def add_noise(self) -> None:
        rng = self.rng
        weekly = [n for n in NOISE if "Weekly" in n[0] or "Digest" in n[0] or "bulletin" in n[3]]
        t = T_START
        i = 0
        while t < T_NOW:
            for n in weekly:
                ts = t + timedelta(days=rng.randrange(7), hours=rng.randrange(7, 11), minutes=rng.randrange(60))
                if ts < T_NOW:
                    self._noise_msg(n, ts, i)
                    i += 1
            t += timedelta(days=7)
        others = [n for n in NOISE if n not in weekly]
        for _ in range(round(300 * self.scale)):
            n = rng.choice(others)
            self._noise_msg(n, recent_bias_time(rng, T_START, T_NOW), i)
            i += 1
        # A digest that corroborates a fact (the agent may cite it)
        self.add(source="gmail", chat="noise_digest_payloop", sender="noise:Montreal Startup Digest|hello@mtlstartupdigest.ca|CATEGORY_UPDATES",
                 ts=local("2026-10-15 07:00"), subject="Montreal Startup Digest — Payloop raises $8M",
                 text="Payloop, the Montreal payments startup founded by Omar Haddad, raised an $8M Series A led by Maple Ridge Ventures.",
                 kind="noise", facts=("omar_h.series_a",))

    def _noise_msg(self, n: tuple, ts: datetime, i: int) -> None:
        name, addr, label, subj, body = n
        fmt = {"n": 40 + i % 60, "amt": f"{self.rng.randint(8, 140)}.{self.rng.randint(0, 99):02d}", "code": f"{self.rng.randint(0, 999999):06d}"}
        self.add(source="gmail", chat=f"noise_{i}", sender=f"noise:{name}|{addr}|{label}", ts=ts,
                 subject=subj.format(**fmt), text=body.format(**fmt), kind="noise")

    def run(self) -> None:
        self.add_beats()
        self.add_filler()
        self.add_groups()
        self.add_noise()
        self.msgs.sort(key=lambda m: (m.ts, m.key))


# --------------------------------------------------------------------------- calendar
def calendar_events(w: World) -> list[dict]:
    rng = random.Random(7)
    me = OWNER.emails[0][0]
    P = w.people
    out: list[dict] = []

    def eid() -> str:
        return "".join(rng.choice("abcdefghijklmnopqrstuv0123456789") for _ in range(26))

    def ev(summary, start, end=None, *, attendees=(), location="", description="", organizer=None, facts=(),
           status="confirmed", all_day=False, video=False, recurring=None):
        e = {"kind": "calendar#event", "etag": f"\"{rng.randrange(10**15, 10**16)}\"", "id": eid(), "status": status,
             "htmlLink": "https://www.google.com/calendar/event?eid=" + base64.b64encode(rng.randbytes(12)).decode().rstrip("="),
             "summary": summary, "creator": {"email": me, "self": True}}
        org_email = organizer or me
        e["organizer"] = {"email": org_email, **({"self": True} if org_email == me else {})}
        if all_day:
            e["start"] = {"date": start}
            e["end"] = {"date": end}
        else:
            s, en = local(start), local(end)
            e["start"] = {"dateTime": s.isoformat(), "timeZone": OWNER_TZ}
            e["end"] = {"dateTime": en.isoformat(), "timeZone": OWNER_TZ}
        if location:
            e["location"] = location
        if description:
            e["description"] = description
        if attendees:
            e["attendees"] = [{"email": me, "self": True, "responseStatus": "accepted", **({"organizer": True} if org_email == me else {})}]
            for a in attendees:
                if "@" in a:
                    e["attendees"].append({"email": a, "responseStatus": "needsAction"})
                else:
                    p = P[a]
                    e["attendees"].append({"email": p.emails[-1][0], "displayName": p.name, "responseStatus": "accepted",
                                           **({"organizer": True} if p.emails[-1][0] == org_email else {})})
        if video:
            code = "-".join("".join(rng.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(k)) for k in (3, 4, 3))
            e["hangoutLink"] = f"https://meet.google.com/{code}"
        if recurring:
            e["recurringEventId"] = recurring
        e["created"] = "2026-01-01T00:00:00.000Z"
        e["updated"] = "2026-10-01T00:00:00.000Z"
        e["iCalUID"] = e["id"] + "@google.com"
        e["sequence"] = 0
        e["reminders"] = {"useDefault": True}
        e["eventType"] = "default"
        e["_facts"] = list(facts)
        out.append(e)
        return e

    # Upcoming, fact-carrying events
    ev("Coffee w/ Omar Haddad", "2026-10-28 10:00", "2026-10-28 10:45", attendees=["omar_h"], location="Café Névé, 151 Rue Rachel E, Montréal", facts=["omar_h.coffee"])
    ev("Tidepool pitch — Maple Ridge Ventures", "2026-10-29 15:00", "2026-10-29 16:00", attendees=["daniel_vc", "mei"],
       location="Maple Ridge Ventures, 1250 Boul. René-Lévesque O, 18e étage", organizer="daniel@mapleridge.vc", facts=["daniel_vc.pitch"])
    ev("Plumber — bathroom sink", "2026-10-30 09:00", "2026-10-30 12:00", location="4520 rue Saint-Denis", description="Key with the concierge", facts=["omar_k.plumber"])
    ev("Aldridge & Co. — Final round interview", "2026-10-30 13:00", "2026-10-30 14:30", attendees=["hannah"], organizer="hannah.weiss@aldridgeco.com", video=True, facts=["hannah.final"])
    ev("Strategy term paper due", "2026-11-02", "2026-11-03", all_day=True, facts=["ruth.extension"])
    ev("Énergie Boréale — appel de présentation", "2026-11-03 14:00", "2026-11-03 15:00", attendees=["elodie", "innovation@energieboreale.ca", "mei"], organizer="elodie.gagnon@hydrolab.ca", video=True, facts=["elodie.boreale"])
    ev("🎂 Isabelle", "2026-11-03", "2026-11-04", all_day=True, facts=["isabelle.bday"])
    ev("Souper fête d'Isabelle", "2026-11-03 19:30", "2026-11-03 22:00", location="Le Petit Bistro, Rue Saint-Denis", facts=["isabelle.bday"])
    ev("Stellarfin × Tidepool pilot kickoff", "2026-11-04 13:00", "2026-11-04 14:00", attendees=["priya", "mei"], video=True, facts=["priya.pilot"])
    ev("Mei OOO — Japan", "2026-11-05", "2026-11-16", all_day=True, facts=["mei.japan"])
    ev("Northwind <> Tidepool", "2026-11-06 11:00", "2026-11-06 11:45", attendees=["rachel", "tyler"], organizer="tyler@northwindcap.com", video=True, facts=["rachel.meeting"])
    ev("Desautels Case Competition", "2026-11-07 08:30", "2026-11-07 17:00", attendees=["omar_b"], location="Bronfman Building, 1001 Sherbrooke O", facts=["omar_b.casecomp"])
    ev("Dan & Jess wedding 💍", "2027-06-12", "2027-06-13", all_day=True, location="Toronto", facts=["daniel_cl.wedding"])
    # Cancelled event (API returns it only with showDeleted=true)
    ev("Lunch w/ Priya", "2026-10-21 12:00", "2026-10-21 13:00", attendees=["priya"], status="cancelled", facts=["priya.cancelled_lunch"])
    # Past one-offs
    ev("Startupfest", "2026-07-15", "2026-07-18", all_day=True, location="Montréal", facts=["rachel.met"])
    ev("Maple Ridge founders breakfast", "2026-10-06 08:00", "2026-10-06 09:30", attendees=["daniel_vc"], location="Maple Ridge Ventures")
    ev("Coffee w/ Omar Haddad", "2026-06-17 09:30", "2026-06-17 10:15", attendees=["omar_h"], location="Café Névé")
    ev("Coffee w/ Tom Becker (Lumen Grid)", "2026-02-12 08:30", "2026-02-12 09:15", attendees=["tom"], location="Hotel Bonaventure lobby", facts=["tom.intro"])
    ev("Thanksgiving — Sherbrooke", "2026-10-12", "2026-10-13", all_day=True, location="Sherbrooke")
    ev("Hydrolab intro call", "2026-09-02 10:00", "2026-09-02 10:45", attendees=["elodie"], video=True)
    ev("Office hours — Prof. Abernathy", "2026-10-06 14:00", "2026-10-06 14:20", attendees=["ruth"], location="Bronfman 518")
    # Recurring: weekly sync with Mei (Mondays 9:30) and Strategy class (Tue/Thu 8:35)
    rec_sync = eid()
    d = local("2025-09-08 09:30")
    while d < local("2026-12-21 09:30"):
        if not (local("2026-11-05 00:00") <= d <= local("2026-11-16 00:00")):
            ev("Tidepool weekly sync", d.strftime("%Y-%m-%d %H:%M"), (d + timedelta(minutes=45)).strftime("%Y-%m-%d %H:%M"),
               attendees=["mei"], video=True, recurring=rec_sync)
        d += timedelta(days=7)
    rec_class = eid()
    for term_start, term_end in (("2025-09-02", "2025-12-04"), ("2026-01-06", "2026-04-09"), ("2026-09-01", "2026-12-03")):
        d0 = datetime.strptime(term_start, "%Y-%m-%d").date()
        d1 = datetime.strptime(term_end, "%Y-%m-%d").date()
        while d0 <= d1:
            if d0.weekday() in (1, 3):
                ev("MGCR 701 — Strategy" if term_start.startswith("2026-09") else "MBA core class", f"{d0} 08:35", f"{d0} 09:55",
                   location="Bronfman 151", recurring=rec_class)
            d0 += timedelta(days=1)
    # Gym with Yusuf, some Sundays
    rec_run = eid()
    d = local("2026-08-02 08:00")
    while d < T_NOW:
        if rng.random() < 0.6:
            ev("Long run w/ Yusuf", d.strftime("%Y-%m-%d %H:%M"), (d + timedelta(minutes=100)).strftime("%Y-%m-%d %H:%M"),
               location="Mount Royal — George-Étienne Cartier monument", recurring=rec_run)
        d += timedelta(days=7)
    return out


# --------------------------------------------------------------------------- exporters
class Export:
    def __init__(self, w: World, out: Path) -> None:
        self.w = w
        self.out = out
        self.rng = random.Random(99)
        self.P = w.people

    # ---------------- Gmail
    def addr(self, pid: str, when: datetime) -> tuple[str, str]:
        if pid == "me":
            return OWNER.name, OWNER.emails[0][0]
        p = self.P[pid]
        return p.name, valid_at(p.emails, when) or ""

    def gmail(self) -> None:
        d = self.out / "raw" / "gmail"
        d.mkdir(parents=True, exist_ok=True)
        threads: dict[str, list[Msg]] = {}
        for m in self.w.msgs:
            if m.source == "gmail":
                threads.setdefault(m.chat, []).append(m)
        lines = []
        hist = 9_800_000
        for chat, msgs in threads.items():
            thread_id = None
            prev: Msg | None = None
            for m in msgs:
                mid = gmail_id(self.rng)
                thread_id = thread_id or mid
                hist += self.rng.randint(1, 300)
                if m.sender.startswith("noise:"):
                    name, address, label = m.sender[6:].split("|")
                    frm = f"{name} <{address}>"
                    to_hdr = OWNER.emails[0][0]
                    cc_hdr = ""
                    labels = ["INBOX", label] + (["UNREAD"] if self.rng.random() < 0.6 else [])
                else:
                    sname, saddr = self.addr(m.sender, m.ts)
                    frm = f'"{sname}" <{saddr}>' if "'" in sname or "," in sname else f"{sname} <{saddr}>"
                    if m.to:
                        tos = [self.addr(t, m.ts) for t in m.to]
                    elif m.sender == "me":
                        tos = [self.addr(m.raw_ref.get("person") or m.chat, m.ts)]
                    else:
                        tos = [self.addr("me", m.ts)]
                    to_hdr = ", ".join(f"{n} <{a}>" for n, a in tos)
                    cc_hdr = ", ".join(f"{n} <{a}>" for n, a in (self.addr(c, m.ts) for c in m.cc))
                    labels = ["SENT"] if m.sender == "me" else ["INBOX", "CATEGORY_PERSONAL"] + (["IMPORTANT"] if m.kind == "beat" else [])
                body = m.text
                if prev is not None and not m.sender.startswith("noise:"):
                    pn, pa = self.addr(prev.sender, prev.ts)
                    quoted = "\n".join("> " + ln for ln in prev.text.splitlines())
                    stamp = f"{prev.ts:%a, %b} {prev.ts.day}, {prev.ts:%Y} at {us_time(prev.ts)}"
                    body += f"\n\nOn {stamp} {pn} <{pa}> wrote:\n{quoted}"
                plain_first = " ".join(m.text.split())
                snippet = html.escape(plain_first[:200], quote=True).replace("&#x27;", "&#39;")
                headers = [
                    {"name": "Delivered-To", "value": OWNER.emails[0][0]},
                    {"name": "MIME-Version", "value": "1.0"},
                    {"name": "Date", "value": m.ts.strftime("%a, %d %b %Y %H:%M:%S %z")},
                    {"name": "Message-ID", "value": f"<{mid}.{hashlib.md5(mid.encode()).hexdigest()[:10]}@mail.example>"},
                    {"name": "Subject", "value": m.subject},
                    {"name": "From", "value": frm},
                    {"name": "To", "value": to_hdr},
                ]
                if cc_hdr:
                    headers.append({"name": "Cc", "value": cc_hdr})
                if prev is not None:
                    headers.append({"name": "In-Reply-To", "value": f"<{prev.raw_ref['gmail_id']}@mail.example>"})
                headers.append({"name": "Content-Type", "value": 'multipart/alternative; boundary="000000000000a1b2c3"'})
                html_body = "<div dir=\"ltr\">" + html.escape(body).replace("\n", "<br>") + "</div>"
                msg = {
                    "id": mid, "threadId": thread_id, "labelIds": labels, "snippet": snippet,
                    "sizeEstimate": len(body) + 1800, "historyId": str(hist),
                    "internalDate": str(int(m.ts.timestamp() * 1000)),
                    "payload": {
                        "partId": "", "mimeType": "multipart/alternative", "filename": "", "headers": headers,
                        "body": {"size": 0},
                        "parts": [
                            {"partId": "0", "mimeType": "text/plain", "filename": "", "headers": [{"name": "Content-Type", "value": "text/plain; charset=\"UTF-8\""}],
                             "body": {"size": len(body.encode()), "data": b64url(body)}},
                            {"partId": "1", "mimeType": "text/html", "filename": "", "headers": [{"name": "Content-Type", "value": "text/html; charset=\"UTF-8\""}],
                             "body": {"size": len(html_body.encode()), "data": b64url(html_body)}},
                        ],
                    },
                }
                m.raw_ref.update({"gmail_id": mid, "thread_id": thread_id, "labels": labels})
                lines.append(json.dumps(msg, ensure_ascii=False))
                prev = m
        (d / "messages.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="")
        (d / "profile.json").write_text(json.dumps({"emailAddress": OWNER.emails[0][0], "messagesTotal": len(lines),
                                                    "threadsTotal": len(threads), "historyId": str(hist)}, indent=2), encoding="utf-8")

    # ---------------- iMessage chat.db
    def imessage(self) -> None:
        d = self.out / "raw" / "imessage"
        d.mkdir(parents=True, exist_ok=True)
        path = d / "chat.db"
        if path.exists():
            path.unlink()
        con = sqlite3.connect(path)
        con.executescript(
            """
            CREATE TABLE handle (ROWID INTEGER PRIMARY KEY AUTOINCREMENT UNIQUE, id TEXT NOT NULL, country TEXT, service TEXT NOT NULL, uncanonicalized_id TEXT, person_centric_id TEXT, UNIQUE (id, service));
            CREATE TABLE chat (ROWID INTEGER PRIMARY KEY AUTOINCREMENT, guid TEXT UNIQUE NOT NULL, style INTEGER, state INTEGER, account_id TEXT, chat_identifier TEXT, service_name TEXT, room_name TEXT, display_name TEXT, is_archived INTEGER DEFAULT 0);
            CREATE TABLE chat_handle_join (chat_id INTEGER REFERENCES chat (ROWID) ON DELETE CASCADE, handle_id INTEGER REFERENCES handle (ROWID) ON DELETE CASCADE, UNIQUE(chat_id, handle_id));
            CREATE TABLE message (ROWID INTEGER PRIMARY KEY AUTOINCREMENT, guid TEXT UNIQUE NOT NULL, text TEXT, replace INTEGER DEFAULT 0, service_center TEXT, handle_id INTEGER DEFAULT 0, subject TEXT, attributedBody BLOB, version INTEGER DEFAULT 0, type INTEGER DEFAULT 0, service TEXT, account TEXT, date INTEGER, date_read INTEGER, date_delivered INTEGER, is_delivered INTEGER DEFAULT 0, is_from_me INTEGER DEFAULT 0, is_read INTEGER DEFAULT 0, cache_has_attachments INTEGER DEFAULT 0, associated_message_guid TEXT, associated_message_type INTEGER DEFAULT 0, thread_originator_guid TEXT);
            CREATE TABLE chat_message_join (chat_id INTEGER REFERENCES chat (ROWID) ON DELETE CASCADE, message_id INTEGER REFERENCES message (ROWID) ON DELETE CASCADE, message_date INTEGER DEFAULT 0, PRIMARY KEY (chat_id, message_id));
            """
        )
        handle_rows: dict[str, int] = {}
        chat_rows: dict[str, int] = {}
        last_guid: dict[str, str] = {}

        def handle_id(value: str) -> int:
            if value not in handle_rows:
                cur = con.execute("INSERT INTO handle (id, country, service, uncanonicalized_id) VALUES (?,?,?,?)", (value, "ca", "iMessage", value))
                handle_rows[value] = cur.lastrowid
            return handle_rows[value]

        def chat_id(value: str) -> int:
            if value not in chat_rows:
                cur = con.execute("INSERT INTO chat (guid, style, state, account_id, chat_identifier, service_name, display_name) VALUES (?,?,?,?,?,?,?)",
                                  (f"iMessage;-;{value}", 45, 3, "E:" + OWNER.emails[0][0], value, "iMessage", ""))
                chat_rows[value] = cur.lastrowid
                con.execute("INSERT INTO chat_handle_join VALUES (?,?)", (cur.lastrowid, handle_id(value)))
            return chat_rows[value]

        for m in self.w.msgs:
            if m.source != "imessage":
                continue
            p = self.P[m.chat]
            value = valid_at(p.phones, m.ts) or p.emails[0][0]
            cid = chat_id(value)
            hid = handle_id(value)
            guid = "".join(self.rng.choice("0123456789ABCDEF") for _ in range(32))
            guid = f"{guid[:8]}-{guid[8:12]}-{guid[12:16]}-{guid[16:20]}-{guid[20:]}"
            apple_ns = int((m.ts.astimezone(UTC) - APPLE_EPOCH).total_seconds() * 1e9)
            # Since macOS Ventura most rows keep text only in attributedBody
            use_blob = m.ts.date() >= date(2025, 11, 1) and self.rng.random() < 0.6
            text_col = None if use_blob else m.text
            blob = typedstream(m.text) if (use_blob or self.rng.random() < 0.5) else None
            assoc_type = 2000 if m.kind == "tapback" else 0
            assoc_guid = f"p:0/{last_guid.get(value, '')}" if m.kind == "tapback" else None
            cur = con.execute(
                "INSERT INTO message (guid, text, handle_id, attributedBody, service, account, date, date_read, date_delivered, is_delivered, is_from_me, is_read, associated_message_guid, associated_message_type) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (guid, text_col, hid, blob, "iMessage", "E:" + OWNER.emails[0][0], apple_ns, apple_ns + 60_000_000_000, apple_ns + 2_000_000_000,
                 1, 1 if m.sender == "me" else 0, 1, assoc_guid, assoc_type),
            )
            con.execute("INSERT INTO chat_message_join VALUES (?,?,?)", (cid, cur.lastrowid, apple_ns))
            if m.kind != "tapback":
                last_guid[value] = guid
            m.raw_ref = {"rowid": cur.lastrowid, "handle": value, "text_in_attributedBody_only": text_col is None}
        con.commit()
        con.close()

    # ---------------- Messenger (Facebook export)
    def messenger(self) -> None:
        base = self.out / "raw" / "messenger" / "inbox"
        threads: dict[str, list[Msg]] = {}
        for m in self.w.msgs:
            if m.source == "messenger":
                threads.setdefault(m.chat, []).append(m)

        def moji(s: str) -> str:
            # Facebook exports write UTF-8 bytes as latin-1 code points
            return s.encode("utf-8").decode("latin-1")

        for chat, msgs in threads.items():
            if chat in GROUPS:
                title = GROUPS[chat]["name"]
                parts = [self.P[x].msgr_name for x in GROUPS[chat]["members"]] + [OWNER.msgr_name]
            else:
                title = self.P[chat].msgr_name
                parts = [title, OWNER.msgr_name]
            import unicodedata
            ascii_title = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
            folder = "".join(ch for ch in ascii_title.lower() if ch.isalnum()) + f"_{self.rng.randint(10**15, 10**16 - 1)}"
            out_msgs = []
            for m in sorted(msgs, key=lambda x: x.ts, reverse=True):  # newest first, like the real export
                sender = OWNER.msgr_name if m.sender == "me" else self.P[m.sender].msgr_name
                ts_ms = int(m.ts.timestamp() * 1000)
                entry = {"sender_name": moji(sender), "timestamp_ms": ts_ms, "is_geoblocked_for_viewer": False, "is_unsent_image_by_messenger_kid_parent": False}
                if m.kind == "media":
                    entry["photos"] = [{"uri": f"messages/inbox/{folder}/photos/{ts_ms}.jpg", "creation_timestamp": ts_ms // 1000}]
                else:
                    entry["content"] = moji(m.text)
                    if self.rng.random() < 0.08:
                        entry["reactions"] = [{"reaction": moji("❤"), "actor": moji(OWNER.msgr_name)}]
                out_msgs.append(entry)
                m.raw_ref = {"thread_path": f"inbox/{folder}", "timestamp_ms": ts_ms}
            doc = {"participants": [{"name": moji(n)} for n in parts], "messages": out_msgs, "title": moji(title),
                   "is_still_participant": True, "thread_path": f"inbox/{folder}", "magic_words": [],
                   "joinable_mode": {"mode": 1, "link": ""}}
            (base / folder).mkdir(parents=True, exist_ok=True)
            (base / folder / "message_1.json").write_text(json.dumps(doc, indent=2, ensure_ascii=True), encoding="utf-8", newline="")

    # ---------------- Contacts (vCard)
    def contacts(self) -> None:
        d = self.out / "raw" / "contacts"
        d.mkdir(parents=True, exist_ok=True)
        cards = []
        for p in self.P.values():
            if p.pid == "owner" or p.card is None:
                continue
            c = p.card
            first, last = (p.name.split(" ", 1) + [""])[:2]
            fn = c.get("fn", p.name)
            if fn != p.name:  # saved under a custom label: N mirrors what the user typed
                f_first, f_last = (fn.split(" ", 1) + [""])[:2]
            else:
                f_first, f_last = first, last
            lines = ["BEGIN:VCARD", "VERSION:3.0", f"N:{f_last};{f_first};;;", f"FN:{fn}"]
            if c.get("nickname"):
                lines.append(f"NICKNAME:{c['nickname']}")
            if c.get("org"):
                lines.append(f"ORG:{c['org']}")
            if c.get("title"):
                lines.append(f"TITLE:{c['title']}")
            for e, frm, to in p.emails:
                if p.pid == "priya" and to == "":  # stale card: only the old address
                    continue
                lines.append(f"EMAIL;TYPE=INTERNET:{e}")
            phones = p.phones[:1] if c.get("phones_only_old") else p.phones
            for ph, _, _ in phones:
                lines.append(f"TEL;TYPE=CELL:{fmt_phone_display(ph)}")
            if c.get("city"):
                lines.append(f"ADR;TYPE=HOME:;;;{c['city']};;;")
            if c.get("bday"):
                lines.append(f"BDAY:{c['bday']}")
            lines.append("END:VCARD")
            cards.append("\r\n".join(lines))
        (d / "contacts.vcf").write_text("\r\n".join(cards) + "\r\n", encoding="utf-8", newline="")

    # ---------------- Calendar
    def calendar(self) -> list[dict]:
        d = self.out / "raw" / "calendar"
        d.mkdir(parents=True, exist_ok=True)
        events = calendar_events(self.w)
        items = [{k: v for k, v in e.items() if not k.startswith("_")} for e in events]
        doc = {"kind": "calendar#events", "etag": "\"p33c9tq0n6g0p80o\"", "summary": OWNER.emails[0][0], "updated": "2026-10-26T21:58:12.000Z",
               "timeZone": OWNER_TZ, "accessRole": "owner", "defaultReminders": [{"method": "popup", "minutes": 10}], "items": items}
        (d / "events.json").write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8", newline="")
        return events


def typedstream(text: str) -> bytes:
    """Minimal NSAttributedString typedstream like macOS writes into message.attributedBody."""
    raw = text.encode("utf-8")
    n = len(raw)
    length = bytes([n]) if n < 0x80 else b"\x81" + n.to_bytes(2, "little")
    return (b"\x04\x0bstreamtyped\x81\xe8\x03\x84\x01@\x84\x84\x84\x12NSAttributedString\x00\x84\x84\x08NSObject\x00\x85\x92"
            b"\x84\x84\x84\x08NSString\x01\x94\x84\x01+" + length + raw +
            b"\x86\x84\x02iI\x01" + bytes([min(n, 255)]) + b"\x92\x84\x84\x84\x0cNSDictionary\x00\x94\x84\x01i\x01\x92\x86\x86")


# --------------------------------------------------------------------------- answer key
def answer_key(w: World, events: list[dict], out: Path) -> None:
    gt = out / "ground_truth"
    gt.mkdir(parents=True, exist_ok=True)
    by_fact: dict[str, list[dict]] = {f: [] for f in FACTS}
    with (gt / "messages.jsonl").open("w", encoding="utf-8", newline="") as fh:
        for m in w.msgs:
            row = {"key": m.key, "source": m.source, "chat": m.chat, "sender": m.sender, "ts_local": m.ts.isoformat(),
                   "ts_utc": m.ts.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "kind": m.kind, "text": m.text,
                   "subject": m.subject, "facts": list(m.facts), "raw_ref": m.raw_ref}
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            for f in m.facts:
                by_fact[f].append({"key": m.key, "source": m.source, "raw_ref": m.raw_ref})
    for e in events:
        for f in e.get("_facts", []):
            by_fact[f].append({"key": None, "source": "calendar", "raw_ref": {"event_id": e["id"], "summary": e["summary"], "status": e["status"]}})
    facts = {fid: {"person": pid, "statement": st, "evidence": by_fact[fid]} for fid, (pid, st) in FACTS.items()}
    missing = [f for f, v in facts.items() if not v["evidence"]]
    if missing:
        raise SystemExit(f"facts with no evidence: {missing}")
    (gt / "facts.json").write_text(json.dumps(facts, indent=2, ensure_ascii=False), encoding="utf-8", newline="")

    people = {}
    for p in w.people.values():
        handles = []
        for e, frm, to in p.emails:
            handles.append({"kind": "email", "value": e, "source": "gmail", "valid_from": frm, "valid_to": to})
        for ph, frm, to in p.phones:
            for src in ["imessage"] if p.imessage else []:
                handles.append({"kind": "phone", "value": ph, "source": src, "valid_from": frm, "valid_to": to})
        labels = sorted({x for x in [p.name, p.phone_label, p.msgr_name, (p.card or {}).get("nickname")] if x})
        if p.pid == "yusuf":
            labels.append(fmt_phone_display("+15145550171"))
        people[p.pid] = {
            "name": p.name, "relationship": p.rel, "org": p.org, "title": p.title, "city": p.city,
            "display_labels": labels, "handles": handles,
            "contact_card": p.card, "facts": [f for f, (pid, _) in FACTS.items() if pid == p.pid],
            "message_count": sum(1 for m in w.msgs if m.chat == p.pid or m.raw_ref.get("person") == p.pid or m.sender == p.pid),
        }
    (gt / "people.json").write_text(json.dumps(people, indent=2, ensure_ascii=False), encoding="utf-8", newline="")
    (gt / "questions.json").write_text(json.dumps(QUESTIONS, indent=2, ensure_ascii=False), encoding="utf-8", newline="")
    (gt / "fact_keywords.json").write_text(json.dumps(FACT_KEYWORDS, indent=2, ensure_ascii=False), encoding="utf-8", newline="")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default="data/synthetic")
    ap.add_argument("--seed", type=int, default=20261026)
    ap.add_argument("--scale", type=float, default=1.6, help="multiplies filler volume")
    args = ap.parse_args()
    out = Path(args.out)
    w = World(args.seed, args.scale)
    w.run()
    ex = Export(w, out)
    ex.gmail()
    ex.imessage()
    ex.messenger()
    ex.contacts()
    events = ex.calendar()
    answer_key(w, events, out)
    counts: dict[str, int] = {}
    for m in w.msgs:
        counts[m.source] = counts.get(m.source, 0) + 1
    print(f"people={len(w.people) - 1} messages={len(w.msgs)} {counts} calendar_events={len(events)} facts={len(FACTS)} questions={len(QUESTIONS)}")


if __name__ == "__main__":
    main()
