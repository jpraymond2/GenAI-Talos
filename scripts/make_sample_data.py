#!/usr/bin/env python3
"""Generate a small fictional sample dataset for persona Alex Martin.

Writes CSVs under data/sample/. Message body text goes through
recall.llm.generator.generate(); empty or failed LLM responses fall back to
deterministic templates. Bodies are cached in data/sample/_text_cache.json so
reruns need no API key. A fixed seed and anchor date make output identical
every run.
"""

from __future__ import annotations

import csv
import hashlib
import json
import random
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# Allow `uv run python scripts/make_sample_data.py` from a fresh checkout.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from recall.llm.generator import generate  # noqa: E402

RANDOM_SEED = 42
ANCHOR = datetime(2026, 10, 15, 12, 0, 0, tzinfo=timezone.utc)
WINDOW_DAYS = 120
TARGET_INTERACTIONS = 400
TARGET_EVENTS = 25
OUT_DIR = _ROOT / "data" / "sample"
CACHE_PATH = OUT_DIR / "_text_cache.json"

ALEX_EMAIL = "alex.martin@mail.mcgill.ca"
ALEX_PHONE = "+15145550100"
ALEX_DISPLAY = "Alex Martin"

KARIM_H_EMAIL = "karim@ledgerly.io"
KARIM_H_PHONE = "+15145550142"
KARIM_S_EMAIL = "karim.saleh@mcgill.ca"
KARIM_S_PHONE = "+15145550143"
KARIM_PWC_EMAIL = "karim@pwc.com"
KARIM_KH_ALT_EMAIL = "kh@ledgerly.io"
KARIM_GMAIL92 = "karim.k92@gmail.com"
LINA_EMAIL = "lina.chen@northbridge.vc"
LINA_EMAIL_ALT = "lchen@northbridge.vc"


@dataclass(frozen=True)
class ContactSpec:
    """One fictional contact to emit."""

    contact_id: str
    full_name: str
    org: str
    title: str
    city: str
    emails: list[str]
    phones: list[str]
    source: str


def _utc(days_ago: float, hour: int = 14, minute: int = 0) -> datetime:
    """Return a UTC timestamp ``days_ago`` days before the fixed anchor."""
    base = ANCHOR - timedelta(days=days_ago)
    return base.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _fmt(ts: datetime) -> str:
    """Format a datetime as an ISO-8601 UTC string."""
    return ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ref(source: str, token: str) -> str:
    """Build a source_ref like ``gmail:18c2f...``."""
    return f"{source}:{token}"


def _token(*parts: str, length: int = 10) -> str:
    """Deterministic hex-ish token from stable parts."""
    digest = hashlib.sha256("|".join(parts).encode()).hexdigest()
    return digest[:length]


def load_text_cache(path: Path) -> dict[str, str]:
    """Load the message-text cache, or return an empty dict."""
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {str(k): str(v) for k, v in raw.items()}


def save_text_cache(path: Path, cache: dict[str, str]) -> None:
    """Persist the message-text cache as sorted JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = {k: cache[k] for k in sorted(cache)}
    path.write_text(json.dumps(ordered, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def message_text(
    cache: dict[str, str],
    key: str,
    prompt: str,
    template: str,
) -> str:
    """Return cached text, else LLM output, else a deterministic template."""
    if key in cache and cache[key].strip():
        return cache[key]
    try:
        llm = generate(
            prompt,
            system=(
                "You write short, realistic personal/professional message bodies "
                "for a fictional Montreal business student named Alex Martin. "
                "No real people. Keep under 80 words."
            ),
        )
    except Exception:
        llm = ""
    text = (llm or "").strip() or template
    cache[key] = text
    return text


def build_contacts() -> list[ContactSpec]:
    """Return the ten fictional contacts, including both Karims and Lina."""
    return [
        ContactSpec(
            contact_id="c_karim_haddad",
            full_name="Karim Haddad",
            org="Ledgerly",
            title="Founder",
            city="Montreal",
            emails=[KARIM_H_EMAIL],
            phones=[KARIM_H_PHONE],
            source="manual",
        ),
        ContactSpec(
            contact_id="c_karim_saleh",
            full_name="Karim Saleh",
            org="PwC Advisory",
            title="Incoming Associate",
            city="Montreal",
            emails=[KARIM_S_EMAIL],
            phones=[KARIM_S_PHONE],
            source="gmail",
        ),
        ContactSpec(
            contact_id="c_lina_chen",
            full_name="Lina Chen",
            org="Northbridge Ventures",
            title="Associate",
            city="Toronto",
            emails=[LINA_EMAIL, LINA_EMAIL_ALT],
            phones=["+14165550188"],
            source="gmail",
        ),
        ContactSpec(
            contact_id="c_sophie_tremblay",
            full_name="Sophie Tremblay",
            org="McGill University",
            title="BCom Student",
            city="Montreal",
            emails=["sophie.tremblay@mail.mcgill.ca"],
            phones=["+15145550111"],
            source="imessage",
        ),
        ContactSpec(
            contact_id="c_marcus_wei",
            full_name="Marcus Wei",
            org="McGill University",
            title="BCom Student",
            city="Montreal",
            emails=["marcus.wei@mail.mcgill.ca"],
            phones=["+15145550112"],
            source="whatsapp",
        ),
        ContactSpec(
            contact_id="c_jordan_blake",
            full_name="Jordan Blake",
            org="Northstar Recruiting",
            title="Campus Recruiter",
            city="Toronto",
            emails=["jordan.blake@northstar-recruiting.com"],
            phones=["+14165550133"],
            source="gmail",
        ),
        ContactSpec(
            contact_id="c_nadine_okonkwo",
            full_name="Nadine Okonkwo",
            org="McGill University",
            title="Professor of Finance",
            city="Montreal",
            emails=["nadine.okonkwo@mcgill.ca"],
            phones=[],
            source="gmail",
        ),
        ContactSpec(
            contact_id="c_maya_martin",
            full_name="Maya Martin",
            org="",
            title="",
            city="Ottawa",
            emails=["maya.martin@example.com"],
            phones=["+16135550177"],
            source="imessage",
        ),
        ContactSpec(
            contact_id="c_diego_alvarez",
            full_name="Diego Alvarez",
            org="McGill University",
            title="BCom Student",
            city="Montreal",
            emails=["diego.alvarez@mail.mcgill.ca"],
            phones=["+15145550119"],
            source="whatsapp",
        ),
        ContactSpec(
            contact_id="c_emma_rossi",
            full_name="Emma Rossi",
            org="McGill University",
            title="BCom Student",
            city="Montreal",
            emails=["emma.rossi@mail.mcgill.ca"],
            phones=["+15145550121"],
            source="imessage",
        ),
    ]


def build_handles(contacts: list[ContactSpec]) -> list[dict[str, str]]:
    """Build handle rows for Alex and every contact channel."""
    rows: list[dict[str, str]] = [
        {
            "handle_id": "h_alex_email",
            "kind": "email",
            "value": ALEX_EMAIL,
            "display_name": ALEX_DISPLAY,
            "source": "gmail",
        },
        {
            "handle_id": "h_alex_phone",
            "kind": "phone",
            "value": ALEX_PHONE,
            "display_name": ALEX_DISPLAY,
            "source": "imessage",
        },
        {
            "handle_id": "h_alex_wa",
            "kind": "wa_jid",
            "value": f"{ALEX_PHONE.lstrip('+')}@s.whatsapp.net",
            "display_name": ALEX_DISPLAY,
            "source": "whatsapp",
        },
        {
            "handle_id": "h_alex_imessage",
            "kind": "imessage",
            "value": ALEX_PHONE,
            "display_name": ALEX_DISPLAY,
            "source": "imessage",
        },
    ]

    # Karim Haddad: distinct display names per channel (identity-merge fixture).
    rows.extend(
        [
            {
                "handle_id": "h_karim_h_email",
                "kind": "email",
                "value": KARIM_H_EMAIL,
                "display_name": "Karim Haddad",
                "source": "gmail",
            },
            {
                "handle_id": "h_karim_h_wa",
                "kind": "wa_jid",
                "value": f"{KARIM_H_PHONE.lstrip('+')}@s.whatsapp.net",
                "display_name": "Karim \U0001f1f1\U0001f1e7",
                "source": "whatsapp",
            },
            {
                "handle_id": "h_karim_h_phone",
                "kind": "phone",
                "value": KARIM_H_PHONE,
                "display_name": "Karim Haddad",
                "source": "contacts",
            },
            {
                "handle_id": "h_karim_h_imessage",
                "kind": "imessage",
                "value": KARIM_H_PHONE,
                "display_name": "Karim Haddad",
                "source": "imessage",
            },
            # Unlinked emails for identity-scoring fixtures (not on contact cards).
            {
                "handle_id": "h_karim_pwc",
                "kind": "email",
                "value": KARIM_PWC_EMAIL,
                "display_name": "Karim",
                "source": "gmail",
            },
            {
                "handle_id": "h_kh_ledgerly",
                "kind": "email",
                "value": KARIM_KH_ALT_EMAIL,
                "display_name": "Karim H.",
                "source": "gmail",
            },
            {
                "handle_id": "h_karim_gmail92",
                "kind": "email",
                "value": KARIM_GMAIL92,
                "display_name": "Karim",
                "source": "gmail",
            },
            {
                "handle_id": "h_karim_generic",
                "kind": "email",
                "value": "karim.catchup@gmail.com",
                "display_name": "Karim",
                "source": "gmail",
            },
        ]
    )

    for contact in contacts:
        if contact.contact_id == "c_karim_haddad":
            continue
        for i, email in enumerate(contact.emails):
            rows.append(
                {
                    "handle_id": f"h_{contact.contact_id}_email_{i}",
                    "kind": "email",
                    "value": email,
                    "display_name": contact.full_name,
                    "source": contact.source if contact.source in {"gmail", "manual"} else "gmail",
                }
            )
        for i, phone in enumerate(contact.phones):
            rows.append(
                {
                    "handle_id": f"h_{contact.contact_id}_phone_{i}",
                    "kind": "phone",
                    "value": phone,
                    "display_name": contact.full_name,
                    "source": "contacts",
                }
            )
            if contact.source == "whatsapp":
                rows.append(
                    {
                        "handle_id": f"h_{contact.contact_id}_wa_{i}",
                        "kind": "wa_jid",
                        "value": f"{phone.lstrip('+')}@s.whatsapp.net",
                        "display_name": contact.full_name,
                        "source": "whatsapp",
                    }
                )
            if contact.source == "imessage":
                rows.append(
                    {
                        "handle_id": f"h_{contact.contact_id}_imessage_{i}",
                        "kind": "imessage",
                        "value": phone,
                        "display_name": contact.full_name,
                        "source": "imessage",
                    }
                )
    return rows


def _interaction(
    *,
    source: str,
    thread_id: str,
    handle_value: str,
    direction: str,
    ts: datetime,
    subject: str,
    text: str,
    token: str,
) -> dict[str, str]:
    """Build one interactions row."""
    return {
        "interaction_id": f"i_{_token(source, token, thread_id, direction, _fmt(ts))}",
        "source": source,
        "thread_id": thread_id,
        "handle_value": handle_value,
        "direction": direction,
        "ts_utc": _fmt(ts),
        "subject": subject,
        "text": text,
        "source_ref": _ref(source, token),
    }


def plant_karim_interactions(cache: dict[str, str]) -> list[dict[str, str]]:
    """Emit the seven planted Karim/Lina facts as concrete interactions + noise."""
    rows: list[dict[str, str]] = []

    # Fact 3a: ~2-month-old Gmail with Montreal signature.
    sig_body = message_text(
        cache,
        "karim_h_gmail_montreal_sig",
        "Write a short founder update email from Karim at Ledgerly ending with a Montreal signature.",
        (
            "Hey Alex — quick update from Ledgerly. We're iterating on the spend dashboard "
            "and would love your take on the student-card use case when you have a sec.\n\n"
            "—\nKarim Haddad\nFounder, Ledgerly\nMontreal, QC"
        ),
    )
    rows.append(
        _interaction(
            source="gmail",
            thread_id="th_kh_product",
            handle_value=KARIM_H_EMAIL,
            direction="in",
            ts=_utc(60, hour=16, minute=22),
            subject="Ledgerly product pulse",
            text=sig_body,
            token=_token("gmail", "kh_sig", "60"),
        )
    )
    rows.append(
        _interaction(
            source="gmail",
            thread_id="th_kh_product",
            handle_value=KARIM_H_EMAIL,
            direction="out",
            ts=_utc(59, hour=11, minute=5),
            subject="Re: Ledgerly product pulse",
            text=message_text(
                cache,
                "alex_reply_product",
                "Alex replies briefly agreeing to look at the student-card use case.",
                "Sounds good — send the latest mock when you can. Happy to skim this week.",
            ),
            token=_token("gmail", "kh_sig_reply", "59"),
        )
    )

    # Fact 3b: WhatsApp 12 days ago — moving to Toronto in January.
    rows.append(
        _interaction(
            source="whatsapp",
            thread_id="th_kh_wa",
            handle_value=KARIM_H_PHONE,
            direction="in",
            ts=_utc(12, hour=19, minute=41),
            subject="",
            text=message_text(
                cache,
                "karim_h_wa_toronto",
                "Karim texts that he is moving to Toronto in January.",
                "btw big news — I'm moving to Toronto in January. Lease is almost signed.",
            ),
            token=_token("whatsapp", "kh_toronto", "12"),
        )
    )

    # Fact 4: Open loop — intro to Lina Chen, 19 days ago; Alex never replied.
    rows.append(
        _interaction(
            source="gmail",
            thread_id="th_kh_lina_intro",
            handle_value=KARIM_H_EMAIL,
            direction="in",
            ts=_utc(19, hour=10, minute=14),
            subject="Intro ask: Lina Chen?",
            text=message_text(
                cache,
                "karim_h_lina_intro",
                "Karim asks Alex for an intro to Lina Chen, a VC associate.",
                (
                    "Alex — any chance you could intro me to Lina Chen at Northbridge? "
                    "She's the associate I mentioned; would love 20 minutes on the seed narrative."
                ),
            ),
            token=_token("gmail", "kh_lina", "19"),
        )
    )

    # Fact 5: WhatsApp ~3 weeks ago — closing seed; asks about internship.
    rows.append(
        _interaction(
            source="whatsapp",
            thread_id="th_kh_wa",
            handle_value=KARIM_H_PHONE,
            direction="in",
            ts=_utc(21, hour=18, minute=3),
            subject="",
            text=message_text(
                cache,
                "karim_h_wa_seed",
                "Karim says he is closing a seed round and asks how Alex's internship search is going.",
                (
                    "we're closing a seed round this month 🤞 how is your internship search going? "
                    "happy to intro you to a couple operators if useful"
                ),
            ),
            token=_token("whatsapp", "kh_seed", "21"),
        )
    )
    rows.append(
        _interaction(
            source="whatsapp",
            thread_id="th_kh_wa",
            handle_value=KARIM_H_PHONE,
            direction="out",
            ts=_utc(21, hour=18, minute=40),
            subject="",
            text=message_text(
                cache,
                "alex_wa_seed_reply",
                "Alex replies about internship search casually.",
                "congrats!! search is okay — a few second rounds. would love intros next week",
            ),
            token=_token("whatsapp", "kh_seed_reply", "21"),
        )
    )

    # Extra Karim Haddad iMessage (identity merge via same phone).
    rows.append(
        _interaction(
            source="imessage",
            thread_id="th_kh_imessage",
            handle_value=KARIM_H_PHONE,
            direction="in",
            ts=_utc(35, hour=20, minute=12),
            subject="",
            text=message_text(
                cache,
                "karim_h_imessage_hang",
                "Karim texts about grabbing coffee in Mile End.",
                "you free for coffee in Mile End sometime? same café as June works",
            ),
            token=_token("imessage", "kh_hang", "35"),
        )
    )

    # Karim Saleh — PwC career messages on WhatsApp / iMessage (identity scoring).
    for days_ago, source, handle, key, template in [
        (
            56,
            "whatsapp",
            f"{KARIM_S_PHONE.lstrip('+')}@s.whatsapp.net",
            "ks_pwc_1",
            "Just accepted — starting at PwC Advisory in January. Wild.",
        ),
        (
            40,
            "imessage",
            KARIM_S_PHONE,
            "ks_pwc_2",
            "Prep for consulting case interviews this weekend if you're free?",
        ),
        (
            23,
            "whatsapp",
            f"{KARIM_S_PHONE.lstrip('+')}@s.whatsapp.net",
            "ks_pwc_3",
            "First client engagement briefing at the firm next month — nervous but excited.",
        ),
        (
            14,
            "whatsapp",
            f"{KARIM_S_PHONE.lstrip('+')}@s.whatsapp.net",
            "ks_pwc_5",
            "PricewaterhouseCoopers badge photo day — still surreal.",
        ),
        (
            10,
            "imessage",
            KARIM_S_PHONE,
            "ks_pwc_6",
            "Coffee with a PwC mentor tomorrow about the advisory track.",
        ),
        (
            7,
            "imessage",
            KARIM_S_PHONE,
            "ks_pwc_4",
            "PwC Advisory onboarding packet landed. January start is real.",
        ),
        (
            6,
            "whatsapp",
            f"{KARIM_S_PHONE.lstrip('+')}@s.whatsapp.net",
            "ks_pwc_review",
            "TA review session Thursday — bring the case notes from last week.",
        ),
    ]:
        rows.append(
            _interaction(
                source=source,
                thread_id="th_ks_pwc",
                handle_value=handle,
                direction="in",
                ts=_utc(days_ago, hour=18, minute=0),
                subject="",
                text=template,
                token=_token(source, "ks_pwc", str(days_ago)),
            )
        )

    # Karim Haddad — Habs game (content congruence for karim.k92@gmail.com).
    rows.append(
        _interaction(
            source="imessage",
            thread_id="th_kh_habs",
            handle_value=KARIM_H_PHONE,
            direction="in",
            ts=_utc(9, hour=22, minute=10),
            subject="",
            text="Habs game Saturday was loud and perfect — thanks for the tickets.",
            token=_token("imessage", "kh_habs", "9"),
        )
    )

    # Unlinked Gmail fixtures for identity scoring (fixed copy; no LLM).
    rows.append(
        _interaction(
            source="gmail",
            thread_id="th_pwc_coffee",
            handle_value=KARIM_PWC_EMAIL,
            direction="in",
            ts=_utc(5, hour=15, minute=0),
            subject="PwC Advisory round",
            text=(
                "Thanks again for the case interview tips last week, the PwC Advisory "
                "round went well. Still on for the review session Thursday?\n\nKarim"
            ),
            token=_token("gmail", "pwc_coffee", "5"),
        )
    )
    rows.append(
        _interaction(
            source="gmail",
            thread_id="th_kh_alt",
            handle_value=KARIM_KH_ALT_EMAIL,
            direction="in",
            ts=_utc(4, hour=11, minute=0),
            subject="Deck follow-up",
            text=(
                "Quick follow-up on the seed deck.\n\n"
                "Karim Haddad\nFounder & CEO, Ledgerly\n+1 (514) 555-0142"
            ),
            token=_token("gmail", "kh_alt", "4"),
        )
    )
    rows.append(
        _interaction(
            source="gmail",
            thread_id="th_karim_gmail92",
            handle_value=KARIM_GMAIL92,
            direction="in",
            ts=_utc(3, hour=9, minute=0),
            subject="Habs",
            text="Great seeing you at the Habs game Saturday! Let's grab dinner soon.",
            token=_token("gmail", "karim_k92", "3"),
        )
    )
    rows.append(
        _interaction(
            source="gmail",
            thread_id="th_karim_generic",
            handle_value="karim.catchup@gmail.com",
            direction="in",
            ts=_utc(2, hour=10, minute=0),
            subject="Hi",
            text="Hope you're well, let's catch up.",
            token=_token("gmail", "karim_generic", "2"),
        )
    )

    # Karim Saleh — Gmail course thread noise + a few planted.
    for days_ago, subject, key, template in [
        (
            8,
            "FINA 341 — office hours this week",
            "saleh_oh",
            "Hi Alex, office hours Thursday 3–5pm in Bronfman 310. Bring midterm questions.",
        ),
        (
            27,
            "Re: FINA 341 problem set 4",
            "saleh_ps4",
            "Your WACC setup looks right; double-check the tax shield on the lease item.",
        ),
        (
            44,
            "FINA 341 — guest speaker reminder",
            "saleh_guest",
            "Reminder: guest speaker Friday. Attendance counts toward participation.",
        ),
    ]:
        rows.append(
            _interaction(
                source="gmail",
                thread_id="th_ks_fina",
                handle_value=KARIM_S_EMAIL,
                direction="in",
                ts=_utc(days_ago, hour=9, minute=30),
                subject=subject,
                text=message_text(
                    cache,
                    key,
                    f"Write a short TA email about: {subject}",
                    template,
                ),
                token=_token("gmail", "ks", str(days_ago)),
            )
        )

    # Fact 7: Lina Chen — a few ordinary emails.
    for days_ago, subject, key, template, direction in [
        (
            40,
            "Northbridge coffee chat follow-up",
            "lina_followup",
            (
                "Great chatting, Alex. Attaching a one-pager on our student-founder thesis. "
                "Happy to reconnect after midterms."
            ),
            "in",
        ),
        (
            39,
            "Re: Northbridge coffee chat follow-up",
            "alex_lina_thanks",
            "Thanks Lina — one-pager was helpful. I'll ping after exams.",
            "out",
        ),
        (
            70,
            "Intro: Alex Martin <> Lina Chen",
            "lina_intro_thread",
            (
                "Alex, looping in Lina Chen (Associate, Northbridge). Lina, Alex is a McGill "
                "BCom student exploring fintech ops roles."
            ),
            "in",
        ),
    ]:
        rows.append(
            _interaction(
                source="gmail",
                thread_id="th_lina",
                handle_value=LINA_EMAIL,
                direction=direction,
                ts=_utc(days_ago, hour=15, minute=10),
                subject=subject,
                text=message_text(
                    cache,
                    key,
                    f"Write a short email body for: {subject}",
                    template,
                ),
                token=_token("gmail", "lina", str(days_ago), direction),
            )
        )

    return rows


def _noise_templates(rng: random.Random, contact: ContactSpec, source: str) -> tuple[str, str]:
    """Return (subject, body template) for ordinary filler traffic."""
    first = contact.full_name.split()[0]
    subjects = {
        "gmail": [
            f"Quick question, {first}",
            "Study group this weekend?",
            "Notes from yesterday",
            "Re: internship deadlines",
            "Syllabus clarification",
        ],
        "whatsapp": ["", "", ""],
        "imessage": ["", "", ""],
    }
    bodies = [
        f"hey — circling back on {contact.org or 'class'} stuff, free later?",
        "can you send the notes when you get a chance?",
        "thanks! that helps a lot",
        "running 10 min late, start without me",
        "did you see the announcement on MyCourses?",
        "coffee before the midterm?",
        "I'll take the first half of the deck",
        "reminder about the group meeting tomorrow",
    ]
    subject = rng.choice(subjects.get(source, [""]))
    body = rng.choice(bodies)
    return subject, body


def fill_interactions(
    cache: dict[str, str],
    contacts: list[ContactSpec],
    planted: list[dict[str, str]],
    rng: random.Random,
) -> list[dict[str, str]]:
    """Pad to TARGET_INTERACTIONS with ordinary multi-channel noise."""
    rows = list(planted)
    noise_contacts = [c for c in contacts if c.contact_id not in {"c_karim_haddad", "c_karim_saleh"}]

    # Extra ordinary traffic with Karim Haddad so he is frequent, not only planted.
    for i in range(25):
        days = rng.uniform(1, WINDOW_DAYS - 1)
        source = rng.choice(["gmail", "whatsapp", "imessage"])
        handle = {
            "gmail": KARIM_H_EMAIL,
            "whatsapp": KARIM_H_PHONE,
            "imessage": KARIM_H_PHONE,
        }[source]
        subject, template = _noise_templates(
            rng,
            next(c for c in contacts if c.contact_id == "c_karim_haddad"),
            source,
        )
        direction = rng.choice(["in", "out"])
        key = f"noise_kh_{i}_{source}_{direction}"
        rows.append(
            _interaction(
                source=source,
                thread_id=f"th_kh_{source}",
                handle_value=handle,
                direction=direction,
                ts=_utc(days, hour=rng.randint(8, 22), minute=rng.randint(0, 59)),
                subject=subject if source == "gmail" else "",
                text=message_text(
                    cache,
                    key,
                    f"Short {source} message between Alex and Karim Haddad.",
                    template,
                ),
                token=_token("noise", "kh", str(i), source),
            )
        )

    channel_plan = [
        ("gmail", "email"),
        ("whatsapp", "phone"),
        ("imessage", "phone"),
    ]
    seq = 0
    while len(rows) < TARGET_INTERACTIONS:
        contact = rng.choice(noise_contacts)
        source, need = rng.choice(channel_plan)
        if need == "email":
            if not contact.emails:
                continue
            handle = contact.emails[0]
            thread = f"th_{contact.contact_id}_mail"
        else:
            if not contact.phones:
                # Fall back to email for phone-less contacts.
                if not contact.emails:
                    continue
                source = "gmail"
                handle = contact.emails[0]
                thread = f"th_{contact.contact_id}_mail"
            else:
                handle = contact.phones[0]
                thread = f"th_{contact.contact_id}_{source}"

        days = rng.uniform(0.5, WINDOW_DAYS - 0.5)
        direction = rng.choice(["in", "out"])
        subject, template = _noise_templates(rng, contact, source)
        key = f"noise_{contact.contact_id}_{seq}"
        rows.append(
            _interaction(
                source=source,
                thread_id=thread,
                handle_value=handle,
                direction=direction,
                ts=_utc(days, hour=rng.randint(7, 23), minute=rng.randint(0, 59)),
                subject=subject if source == "gmail" else "",
                text=message_text(
                    cache,
                    key,
                    f"Short casual {source} message with {contact.full_name}.",
                    template,
                ),
                token=_token("noise", contact.contact_id, str(seq)),
            )
        )
        seq += 1

    rows.sort(key=lambda r: r["ts_utc"])
    return rows[:TARGET_INTERACTIONS]


def build_events(rng: random.Random) -> list[dict[str, str]]:
    """Build TARGET_EVENTS calendar rows, including Coffee w/ Karim in June."""
    events: list[dict[str, str]] = []

    # Fact 6: Coffee w/ Karim in June at a Mile End café.
    coffee_start = datetime(2026, 6, 22, 15, 0, 0, tzinfo=timezone.utc)
    coffee_end = coffee_start + timedelta(minutes=45)
    events.append(
        {
            "event_id": "e_coffee_karim",
            "title": "Coffee w/ Karim",
            "start_utc": _fmt(coffee_start),
            "end_utc": _fmt(coffee_end),
            "location": "Café Olimpico, Mile End, Montreal",
            "attendees": json.dumps([ALEX_EMAIL, KARIM_H_EMAIL]),
            "source_ref": _ref("calendar", _token("cal", "coffee_karim")),
        }
    )

    titles = [
        "FINA 341 lecture",
        "MGCR study group",
        "Office hours — Prof Okonkwo",
        "Career centre drop-in",
        "Internship prep call",
        "Northstar recruiting webinar",
        "Group project sync",
        "Gym",
        "Family dinner",
        "Club executive meeting",
        "Case competition briefing",
        "Coffee w/ Sophie",
        "Library block",
        "Mock interview",
        "Startup meetup — Notman House",
        "TA review session",
        "Dentist",
        "Hackathon planning",
        "Finance club workshop",
        "Zoom: alumni chat",
        "Brunch w/ Maya",
        "Lab booking",
        "Resume clinic",
        "Team standup (case)",
    ]
    locations = [
        "Bronfman Building, McGill",
        "McLennan Library",
        "Zoom",
        "Downtown Montreal",
        "Desautels",
        "",
        "Notman House",
    ]
    attendee_pools = [
        [ALEX_EMAIL],
        [ALEX_EMAIL, "sophie.tremblay@mail.mcgill.ca"],
        [ALEX_EMAIL, "marcus.wei@mail.mcgill.ca"],
        [ALEX_EMAIL, KARIM_S_EMAIL],
        [ALEX_EMAIL, LINA_EMAIL],
        [ALEX_EMAIL, "jordan.blake@northstar-recruiting.com"],
        [ALEX_EMAIL, "maya.martin@example.com"],
    ]

    used_titles = {"Coffee w/ Karim"}
    i = 0
    while len(events) < TARGET_EVENTS:
        title = titles[i % len(titles)]
        if title in used_titles and len(used_titles) < len(titles):
            i += 1
            continue
        used_titles.add(title)
        days = rng.uniform(1, WINDOW_DAYS - 1)
        start = _utc(days, hour=rng.choice([9, 10, 11, 13, 14, 15, 16, 17]), minute=rng.choice([0, 15, 30]))
        end = start + timedelta(minutes=rng.choice([30, 45, 60, 90]))
        events.append(
            {
                "event_id": f"e_{_token('cal', title, str(i))}",
                "title": title,
                "start_utc": _fmt(start),
                "end_utc": _fmt(end),
                "location": rng.choice(locations),
                "attendees": json.dumps(rng.choice(attendee_pools)),
                "source_ref": _ref("calendar", _token("cal", title, str(i))),
            }
        )
        i += 1

    events.sort(key=lambda e: e["start_utc"])
    return events


def contacts_to_rows(contacts: list[ContactSpec]) -> list[dict[str, str]]:
    """Serialize ContactSpec list to CSV-ready dicts."""
    return [
        {
            "contact_id": c.contact_id,
            "full_name": c.full_name,
            "org": c.org,
            "title": c.title,
            "city": c.city,
            "emails": json.dumps(c.emails),
            "phones": json.dumps(c.phones),
            "source": c.source,
        }
        for c in contacts
    ]


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    """Write rows to CSV with stable column order."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fieldnames})


def write_readme(path: Path, counts: dict[str, int]) -> None:
    """Write data/sample/README.md describing the synthetic corpus."""
    path.write_text(
        f"""# Sample data (synthetic)

Fully **fictional** corpus for persona **Alex Martin**, a Montreal business student.
No real personal data. Generated for local SAMPLE_MODE demos and tests.

## Source

Synthetic. Produced by `scripts/make_sample_data.py` with a fixed random seed and
fixed anchor date (`2026-10-15`). Message bodies may be LLM-authored once, then
cached in `_text_cache.json`; with `SAMPLE_MODE=true` (or no key) the script uses
deterministic templates instead.

## Row counts

| File | Rows |
|------|------|
| `contacts.csv` | {counts["contacts"]} |
| `handles.csv` | {counts["handles"]} |
| `interactions.csv` | {counts["interactions"]} |
| `events.csv` | {counts["events"]} |

Total on-disk size of the CSVs is kept under 1 MB.

## How to regenerate

```bash
uv run python scripts/make_sample_data.py
```

Reruns are byte-identical when the text cache is unchanged. Delete
`_text_cache.json` only if you intentionally want new LLM wording (requires a
configured provider and `SAMPLE_MODE=false`).
""",
        encoding="utf-8",
    )


def generate_dataset() -> dict[str, int]:
    """Build all tables, write CSVs + README + text cache, return row counts."""
    rng = random.Random(RANDOM_SEED)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    cache = load_text_cache(CACHE_PATH)

    contacts = build_contacts()
    handles = build_handles(contacts)
    planted = plant_karim_interactions(cache)
    interactions = fill_interactions(cache, contacts, planted, rng)
    events = build_events(rng)
    contact_rows = contacts_to_rows(contacts)

    write_csv(
        OUT_DIR / "handles.csv",
        ["handle_id", "kind", "value", "display_name", "source"],
        handles,
    )
    write_csv(
        OUT_DIR / "interactions.csv",
        [
            "interaction_id",
            "source",
            "thread_id",
            "handle_value",
            "direction",
            "ts_utc",
            "subject",
            "text",
            "source_ref",
        ],
        interactions,
    )
    write_csv(
        OUT_DIR / "events.csv",
        ["event_id", "title", "start_utc", "end_utc", "location", "attendees", "source_ref"],
        events,
    )
    write_csv(
        OUT_DIR / "contacts.csv",
        ["contact_id", "full_name", "org", "title", "city", "emails", "phones", "source"],
        contact_rows,
    )

    counts = {
        "contacts": len(contact_rows),
        "handles": len(handles),
        "interactions": len(interactions),
        "events": len(events),
    }
    write_readme(OUT_DIR / "README.md", counts)
    save_text_cache(CACHE_PATH, cache)
    return counts


def main() -> None:
    """CLI entrypoint."""
    counts = generate_dataset()
    print(
        "Wrote sample data to "
        f"{OUT_DIR}: contacts={counts['contacts']} handles={counts['handles']} "
        f"interactions={counts['interactions']} events={counts['events']}"
    )


if __name__ == "__main__":
    main()
