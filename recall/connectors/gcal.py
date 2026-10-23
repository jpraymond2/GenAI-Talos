"""Google Calendar connector — maps events into recall.events rows."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from recall.config import Settings, get_settings
from recall.connectors.schema import ConnectorResult
from recall.tracing import Tracer


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{digest}"


def _event_instant(payload: dict[str, Any] | None, *, end_of_day: bool = False) -> str:
    """Normalize Calendar start/end (dateTime or all-day date) to UTC ISO-Z."""
    payload = payload or {}
    if payload.get("dateTime"):
        raw = str(payload["dateTime"])
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            return raw
    if payload.get("date"):
        day = str(payload["date"])
        suffix = "23:59:59Z" if end_of_day else "00:00:00Z"
        return f"{day}T{suffix}"
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def map_gcal_event(event: dict[str, Any]) -> dict[str, Any] | None:
    """Map one Calendar API event dict to a recall events row. Pure helper for tests."""
    event_id = str(event.get("id") or "")
    if not event_id:
        return None
    attendees = [
        str(item.get("email") or "").lower()
        for item in (event.get("attendees") or [])
        if item.get("email")
    ]
    location = str(event.get("location") or "")
    title = str(event.get("summary") or "(no title)")
    return {
        "event_id": _stable_id("e", "gcal", event_id),
        "title": title,
        "start_utc": _event_instant(event.get("start")),
        "end_utc": _event_instant(event.get("end"), end_of_day=True),
        "location": location,
        "attendees": json.dumps(attendees),
        "source_ref": f"calendar:{event_id}",
    }


def fetch_gcal(
    settings: Settings | None = None,
    tracer: Tracer | None = None,
) -> ConnectorResult:
    """List primary calendar events into recall rows, or skip with a trace reason."""
    active = settings if settings is not None else get_settings()
    active_tracer = tracer if tracer is not None else Tracer.current()
    result = ConnectorResult(name="gcal")

    try:
        from googleapiclient.discovery import build

        from recall.connectors.google_auth import load_google_credentials

        creds = load_google_credentials(active)
        service = build("calendar", "v3", credentials=creds, cache_discovery=False)
        now = datetime.now(timezone.utc)
        time_min = (now - timedelta(days=180)).isoformat()
        time_max = (now + timedelta(days=30)).isoformat()

        events: list[dict[str, Any]] = []
        request = (
            service.events()
            .list(
                calendarId="primary",
                timeMin=time_min,
                timeMax=time_max,
                singleEvents=True,
                orderBy="startTime",
                maxResults=2500,
            )
        )
        while request is not None:
            page = request.execute()
            for item in page.get("items") or []:
                mapped = map_gcal_event(item)
                if mapped is not None:
                    events.append(mapped)
            request = service.events().list_next(request, page)

        result.events = events
        if active_tracer is not None:
            active_tracer.event("connector", "ingest", {"connector": "gcal", **result.counts()})
        return result
    except Exception as exc:  # noqa: BLE001 - never crash the whole ingest
        reason = _skip_reason(exc)
        result.skipped = reason
        if active_tracer is not None:
            active_tracer.event(
                "connector",
                "skipped",
                {"connector": "gcal", "reason": reason},
            )
        return result


def _skip_reason(exc: Exception) -> str:
    text = str(exc)
    if "credentials.json" in text or isinstance(exc, FileNotFoundError):
        return "skipped: missing Google OAuth credentials"
    return f"skipped: {type(exc).__name__}"
