"""Named SQL reads shared by DuckDB and Zetaris against the recall schema.

Dialect assumption: statements are plain ANSI SQL only — SELECT / WHERE /
ORDER BY / LIMIT, LIKE, IN, and simple comparisons. Placeholders use the
portable ``:name`` form. Backends expand list params to ``IN (?, ?, ...)``
(or their native bind style) and convert markers; see ``sql_bind.py``.
JSON list columns (emails, phones, attendees) and phone / WhatsApp-JID
normalization are filtered in Python after fetch, not in SQL.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from pydantic import BaseModel, Field

from recall.config import Settings, get_settings
from recall.data.query_layer import QueryLayer, get_default_query_layer

# Fixed "now" for SAMPLE_MODE so named-query results stay stable across runs.
SAMPLE_ANCHOR = datetime(2026, 10, 15, 12, 0, 0, tzinfo=timezone.utc)

_HANDLES_BY_DISPLAY_SQL = """
SELECT handle_id, kind, value, display_name, source
FROM recall.handles
WHERE lower(display_name) LIKE :pattern
ORDER BY handle_id
"""

_HANDLES_ALL_SQL = """
SELECT handle_id, kind, value, display_name, source
FROM recall.handles
ORDER BY handle_id
"""

_CONTACTS_BY_NAME_SQL = """
SELECT contact_id, full_name, org, title, city, emails, phones, source
FROM recall.contacts
WHERE lower(full_name) LIKE :pattern
ORDER BY contact_id
"""

_CONTACTS_CANDIDATES_SQL = """
SELECT contact_id, full_name, org, title, city, emails, phones, source
FROM recall.contacts
WHERE lower(full_name) LIKE :pattern
   OR lower(emails) LIKE :email_pattern
ORDER BY contact_id
"""

_INTERACTIONS_SQL = """
SELECT interaction_id, source, thread_id, handle_value, direction,
       ts_utc, subject, text, source_ref
FROM recall.interactions
WHERE handle_value IN (:handle_values)
  AND ts_utc >= :since_ts
ORDER BY ts_utc DESC
LIMIT :limit_n
"""

_EVENTS_WINDOW_SQL = """
SELECT event_id, title, start_utc, end_utc, location, attendees, source_ref
FROM recall.events
WHERE start_utc >= :window_start
  AND start_utc <= :window_end
ORDER BY start_utc
"""


class HandleRow(BaseModel):
    """One identity handle from recall.handles."""

    handle_id: str
    kind: str
    value: str
    display_name: str
    source: str


class InteractionRow(BaseModel):
    """One message or calendar-linked interaction."""

    interaction_id: str
    source: str
    thread_id: str
    handle_value: str
    direction: str
    ts_utc: str
    subject: str = ""
    text: str = ""
    source_ref: str = ""


class EventRow(BaseModel):
    """One calendar event with parsed attendee emails."""

    event_id: str
    title: str
    start_utc: str
    end_utc: str
    location: str = ""
    attendees: list[str] = Field(default_factory=list)
    source_ref: str = ""


class ContactRow(BaseModel):
    """One contact card with parsed emails and phones."""

    contact_id: str
    full_name: str
    org: str = ""
    title: str = ""
    city: str = ""
    emails: list[str] = Field(default_factory=list)
    phones: list[str] = Field(default_factory=list)
    source: str = ""


def resolve_now(
    settings: Settings | None = None,
    now: datetime | None = None,
) -> datetime:
    """Return an explicit now, the sample anchor, or the current UTC time."""
    if now is not None:
        return _as_utc(now)
    active = settings if settings is not None else get_settings()
    if active.sample_mode:
        return SAMPLE_ANCHOR
    return datetime.now(timezone.utc)


def find_handles_by_name(
    name: str,
    *,
    source_tag: str,
    layer: QueryLayer | None = None,
) -> list[HandleRow]:
    """Fuzzy-match handles by display_name and related contacts.full_name."""
    ql = _resolve_layer(layer)
    needle = name.strip().lower()
    pattern = f"%{needle}%"

    by_display = ql.query(
        _HANDLES_BY_DISPLAY_SQL,
        {"pattern": pattern},
        source_tag=f"{source_tag}.display",
    )
    contacts = ql.query(
        _CONTACTS_BY_NAME_SQL,
        {"pattern": pattern},
        source_tag=f"{source_tag}.contacts",
    )
    all_handles = ql.query(_HANDLES_ALL_SQL, source_tag=f"{source_tag}.all_handles")

    matched_digits: set[str] = set()
    matched_values: set[str] = set()
    for contact in contacts:
        for email in _parse_json_list(contact.get("emails")):
            matched_values.add(email)
        for phone in _parse_json_list(contact.get("phones")):
            matched_values.add(phone)
            digits = _digits(phone)
            if digits:
                matched_digits.add(digits)

    selected: dict[str, dict[str, Any]] = {}
    for row in by_display:
        selected[str(row["handle_id"])] = row

    for row in all_handles:
        value = str(row.get("value") or "")
        kind = str(row.get("kind") or "")
        if value in matched_values:
            selected[str(row["handle_id"])] = row
            continue
        if kind == "wa_jid" and _digits(_wa_local_part(value)) in matched_digits:
            selected[str(row["handle_id"])] = row

    ordered = sorted(selected.values(), key=lambda r: str(r["handle_id"]))
    return [HandleRow.model_validate(_stringify_row(row)) for row in ordered]


def interactions_for_handles(
    handle_values: list[str],
    *,
    source_tag: str,
    since_days: int = 120,
    limit: int = 60,
    now: datetime | None = None,
    settings: Settings | None = None,
    layer: QueryLayer | None = None,
) -> list[InteractionRow]:
    """Return newest-first interactions for the given handle values."""
    if not handle_values:
        return []
    active = settings if settings is not None else get_settings()
    ql = _resolve_layer(layer)
    anchor = resolve_now(active, now)
    since_ts = (anchor - timedelta(days=since_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = ql.query(
        _INTERACTIONS_SQL,
        {
            "handle_values": list(handle_values),
            "since_ts": since_ts,
            "limit_n": int(limit),
        },
        source_tag=source_tag,
    )
    return [InteractionRow.model_validate(_stringify_row(row)) for row in rows]


def upcoming_and_past_events(
    emails: list[str],
    *,
    source_tag: str,
    days_back: int = 180,
    days_forward: int = 30,
    now: datetime | None = None,
    settings: Settings | None = None,
    layer: QueryLayer | None = None,
) -> list[EventRow]:
    """Events in [now - days_back, now + days_forward] that include any email."""
    active = settings if settings is not None else get_settings()
    ql = _resolve_layer(layer)
    anchor = resolve_now(active, now)
    window_start = (anchor - timedelta(days=days_back)).strftime("%Y-%m-%dT%H:%M:%SZ")
    window_end = (anchor + timedelta(days=days_forward)).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = ql.query(
        _EVENTS_WINDOW_SQL,
        {
            "window_start": window_start,
            "window_end": window_end,
        },
        source_tag=source_tag,
    )
    wanted = {email.lower() for email in emails}
    events = [_parse_event(row) for row in rows]
    return [
        event
        for event in events
        if any(attendee.lower() in wanted for attendee in event.attendees)
    ]


def contact_cards(
    name_or_email: str,
    *,
    source_tag: str,
    layer: QueryLayer | None = None,
) -> list[ContactRow]:
    """Return every contact matching the name fragment or exact email."""
    ql = _resolve_layer(layer)
    needle = name_or_email.strip()
    if not needle:
        return []
    pattern = f"%{needle.lower()}%"
    rows = ql.query(
        _CONTACTS_CANDIDATES_SQL,
        {"pattern": pattern, "email_pattern": pattern},
        source_tag=source_tag,
    )
    needle_lower = needle.lower()
    matches: list[ContactRow] = []
    for row in rows:
        contact = _parse_contact(row)
        name_hit = needle_lower in contact.full_name.lower()
        email_hit = any(email.lower() == needle_lower for email in contact.emails)
        if name_hit or email_hit:
            matches.append(contact)
    return matches


def _resolve_layer(layer: QueryLayer | None) -> QueryLayer:
    return layer if layer is not None else get_default_query_layer()


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _stringify_row(row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in row.items():
        if value is None:
            out[key] = ""
        elif isinstance(value, datetime):
            out[key] = value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        else:
            out[key] = value
    return out


def _parse_json_list(raw: Any) -> list[str]:
    if raw is None or raw == "":
        return []
    if isinstance(raw, list):
        return [str(item) for item in raw]
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return []
        if isinstance(parsed, list):
            return [str(item) for item in parsed]
    return []


def _parse_event(row: dict[str, Any]) -> EventRow:
    data = _stringify_row(row)
    data["attendees"] = _parse_json_list(row.get("attendees"))
    return EventRow.model_validate(data)


def _parse_contact(row: dict[str, Any]) -> ContactRow:
    data = _stringify_row(row)
    data["emails"] = _parse_json_list(row.get("emails"))
    data["phones"] = _parse_json_list(row.get("phones"))
    return ContactRow.model_validate(data)


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value)


def _wa_local_part(wa_jid: str) -> str:
    return wa_jid.split("@", 1)[0]
