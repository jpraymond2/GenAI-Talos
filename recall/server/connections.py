"""What's connected, Google sign-in from the dashboard, and the upcoming-meetings calendar.

  GET    /api/connections                   every source: connected / ready / off, last sync, how many items
  GET    /api/connect/google/start          returns the Google consent URL (Gmail + Calendar)
  GET    /api/connect/google/callback       Google redirects here; saves secrets/token.json, starts an ingest
  DELETE /api/connections/google            forget the Google token
  GET    /api/calendar/upcoming?days=14     meetings from now on, attendees matched to contact cards

Google sign-in needs a *Web application* OAuth client in secrets/credentials.json
whose authorised redirect URI is ``<RECALL_PUBLIC_URL>/api/connect/google/callback``
(a Desktop client only allows localhost redirects).
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from recall.config import get_settings
from recall.server.auth import require_bearer

router = APIRouter()

MAC_SOURCES = ("imessage", "whatsapp", "contacts")
NAMES = {
    "imessage": "iMessage",
    "whatsapp": "WhatsApp",
    "contacts": "Address book",
    "gmail": "Gmail",
    "calendar": "Google Calendar",
}
_STATE_TTL = timedelta(minutes=15)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- status
def _source_counts() -> dict[str, dict[str, Any]]:
    """Rows and newest timestamp per source, from whichever backend is live. Never raises."""
    out: dict[str, dict[str, Any]] = {}
    try:
        from recall.data.query_layer import get_query_layer

        ql = get_query_layer(get_settings())  # fresh each time so counts move after a pull
        for row in ql.query(
            "SELECT source, COUNT(*) AS n, MAX(ts_utc) AS last_ts FROM recall.interactions GROUP BY source",
            {},
            source_tag="connections",
        ):
            src = str(row.get("source") or "").lower()
            if src:
                out[src] = {"items": int(row.get("n") or 0), "last_ts": str(row.get("last_ts") or "") or None}
        for table, key in (("events", "calendar"), ("contacts", "contacts")):
            rows = ql.query(f"SELECT COUNT(*) AS n FROM recall.{table}", {}, source_tag="connections")
            n = int(rows[0].get("n") or 0) if rows else 0
            if n:
                out.setdefault(key, {"items": 0, "last_ts": None})["items"] = n
    except Exception:  # noqa: BLE001 — status page must load even if the data layer is down
        pass
    return out


def _google_files() -> tuple[bool, bool]:
    from recall.connectors.google_auth import credentials_path, token_path

    return credentials_path().is_file(), token_path().is_file()


def _last_pull_per_source(conn: sqlite3.Connection) -> dict[str, str]:
    last: dict[str, str] = {}
    for row in conn.execute("SELECT params_json, updated_at FROM pulls WHERE status = 'ready' ORDER BY updated_at"):
        for src in json.loads(row["params_json"] or "{}").get("sources", []):
            last[src] = row["updated_at"]
    return last


@router.get("/api/connections", dependencies=[Depends(require_bearer)])
def api_connections() -> dict[str, Any]:
    """One row per source so the dashboard can show what's connected and what isn't."""
    from recall.server import workspace

    conn = workspace._jobs()  # noqa: SLF001
    mac = workspace._device_status(conn)  # noqa: SLF001
    pulled = _last_pull_per_source(conn)
    counts = _source_counts()
    has_creds, has_token = _google_files()
    rows: list[dict[str, Any]] = []

    for src in MAC_SOURCES:
        items = (counts.get(src) or {}).get("items") or 0
        if src in pulled or items:
            status = "connected"
            detail = f"{items:,} {'cards' if src == 'contacts' else 'messages'}" if items else "pulled from your Mac"
            if mac["connected"] and not mac["online"]:
                detail += " · Mac offline"
        elif mac["online"]:
            status, detail = "ready", "Mac connected — pull to add"
        elif mac["connected"]:
            status, detail = "off", "Mac offline"
        else:
            status, detail = "off", "Connect your Mac to sync"
        rows.append({"id": src, "name": NAMES[src], "via": "mac", "status": status, "detail": detail,
                     "items": items or None, "last_sync": pulled.get(src) or (counts.get(src) or {}).get("last_ts"),
                     "action": "pull" if mac["connected"] else "setup_mac"})

    for src in ("gmail", "calendar"):
        items = (counts.get(src) or {}).get("items") or 0
        if has_token or items:
            status = "connected"
            detail = f"{items:,} {'events' if src == 'calendar' else 'emails'}" if items else "signed in — first sync running"
            action = "reconnect"
        else:
            status = "off"
            detail = "Sign in with Google" if has_creds else "Google sign-in not set up on the server"
            action = "connect"
        rows.append({"id": src, "name": NAMES[src], "via": "google", "status": status, "detail": detail,
                     "items": items or None, "last_sync": (counts.get(src) or {}).get("last_ts"), "action": action})

    return {"sources": rows, "mac": mac, "google": {"configured": has_creds, "connected": has_token},
            "connected": sum(1 for r in rows if r["status"] == "connected"), "total": len(rows)}


# --------------------------------------------------------------------------- Google sign-in
def _redirect_uri(request: Request) -> str:
    base = get_settings().recall_public_url.strip().rstrip("/") or str(request.base_url).rstrip("/")
    return f"{base}/api/connect/google/callback"


def _oauth_env(redirect: str) -> None:
    os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")
    if redirect.startswith(("http://localhost", "http://127.0.0.1")):
        os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"


def _states() -> sqlite3.Connection:
    from recall.server import workspace

    conn = workspace._jobs()  # noqa: SLF001
    conn.execute("CREATE TABLE IF NOT EXISTS oauth_states (state TEXT PRIMARY KEY, verifier TEXT, created_at TEXT)")
    return conn


@router.get("/api/connect/google/start", dependencies=[Depends(require_bearer)])
def api_google_start(request: Request) -> dict[str, Any]:
    from recall.connectors.google_auth import SCOPES, credentials_path

    if not credentials_path().is_file():
        raise HTTPException(status_code=503, detail="Google sign-in isn't set up on the server yet (secrets/credentials.json is missing).")
    from google_auth_oauthlib.flow import Flow

    redirect = _redirect_uri(request)
    _oauth_env(redirect)
    flow = Flow.from_client_secrets_file(str(credentials_path()), scopes=SCOPES, redirect_uri=redirect)
    url, state = flow.authorization_url(access_type="offline", prompt="consent", include_granted_scopes="true")
    conn = _states()
    cutoff = _iso(datetime.now(timezone.utc) - _STATE_TTL)
    conn.execute("DELETE FROM oauth_states WHERE created_at < ?", (cutoff,))
    conn.execute("INSERT OR REPLACE INTO oauth_states VALUES (?,?,?)",
                 (state, getattr(flow, "code_verifier", None) or "", _iso(datetime.now(timezone.utc))))
    conn.commit()
    return {"url": url, "redirect_uri": redirect}


@router.get("/api/connect/google/callback")
async def api_google_callback(request: Request, state: str = "", code: str = "", error: str = "") -> RedirectResponse:
    """Google sends the browser back here. The one-time ``state`` (issued to a signed-in user) is the auth."""
    if error:
        return RedirectResponse(f"/?connect_error={quote(error)}")
    conn = _states()
    row = conn.execute("SELECT verifier, created_at FROM oauth_states WHERE state = ?", (state,)).fetchone()
    if row is None or not code:
        return RedirectResponse("/?connect_error=expired")
    conn.execute("DELETE FROM oauth_states WHERE state = ?", (state,))
    conn.commit()

    from google_auth_oauthlib.flow import Flow

    from recall.connectors.google_auth import SCOPES, credentials_path, token_path

    redirect = _redirect_uri(request)
    _oauth_env(redirect)
    try:
        flow = Flow.from_client_secrets_file(str(credentials_path()), scopes=SCOPES, redirect_uri=redirect, state=state)
        if row["verifier"]:
            flow.code_verifier = row["verifier"]
        await asyncio.to_thread(flow.fetch_token, code=code)
        token_path().parent.mkdir(parents=True, exist_ok=True)
        token_path().write_text(flow.credentials.to_json(), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        return RedirectResponse(f"/?connect_error={quote(type(exc).__name__)}")

    if not get_settings().sample_mode:
        from recall.server import app as app_mod

        asyncio.create_task(app_mod._run_live_ingest_once())  # noqa: SLF001 — pull Gmail + Calendar now
    return RedirectResponse("/?connected=google")


@router.delete("/api/connections/google", dependencies=[Depends(require_bearer)])
def api_google_disconnect() -> dict[str, Any]:
    from recall.connectors.google_auth import token_path

    token_path().unlink(missing_ok=True)
    return {"ok": True}


# --------------------------------------------------------------------------- calendar
@router.get("/api/calendar/upcoming", dependencies=[Depends(require_bearer)])
def api_calendar_upcoming(days: int = 14, limit: int = 60) -> dict[str, Any]:
    """Meetings from now on, with attendees matched to contact cards (the owner is left out)."""
    from recall.data.queries import all_events_in_window, resolve_now

    settings = get_settings()
    now = resolve_now(settings)
    days = max(1, min(days, 90))
    try:
        from recall.data.query_layer import get_query_layer

        events = all_events_in_window(source_tag="calendar_view", days_back=1, days_forward=days, now=now, settings=settings,
                                      layer=get_query_layer(settings))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"Calendar unavailable: {type(exc).__name__}") from exc

    people = _email_index()
    owner = _owner_emails() or _likely_owner(settings, now)
    names = _name_index(_all_people())
    out: list[dict[str, Any]] = []
    for ev in events:
        end = _parse(ev.end_utc) or _parse(ev.start_utc)
        if end is None or end < now:
            continue
        if "cancel" in ev.title.lower():
            continue
        attendees = []
        for email in ev.attendees:
            e = email.lower()
            if e in owner:
                continue
            match = people.get(e)
            attendees.append({"email": e, "entity_id": match[0] if match else None, "name": match[1] if match else ""})
        if not any(a["entity_id"] for a in attendees):
            hit = _name_in_title(ev.title, names)
            if hit:
                attendees.append({"email": "", "entity_id": hit[0], "name": hit[1]})
        out.append({"id": ev.event_id, "title": ev.title, "start": ev.start_utc, "end": ev.end_utc,
                    "location": ev.location, "attendees": attendees, "source_ref": ev.source_ref})
        if len(out) >= limit:
            break
    return {"now": _iso(now), "days": days, "events": out}


def _email_index() -> dict[str, tuple[str, str]]:
    """email -> (entity_id, name) from the world model's handle links."""
    try:
        from recall.engines.world.store import WorldStore, world_db_path

        path = world_db_path(get_settings())
        if not path.exists():
            return {}
        store = WorldStore(path=path)
        out: dict[str, tuple[str, str]] = {}
        for eid, name, aliases in store._conn.execute(  # noqa: SLF001
            "SELECT id, canonical_name, aliases_json FROM entities WHERE type = 'person'"
        ):
            for alias in json.loads(aliases or "[]"):
                if "@" in alias and not alias.endswith("@s.whatsapp.net"):
                    out.setdefault(alias.lower(), (eid, name))
        return out
    except Exception:  # noqa: BLE001
        return {}


def _all_people() -> list[tuple[str, str]]:
    try:
        from recall.engines.world.store import WorldStore, world_db_path

        path = world_db_path(get_settings())
        if not path.exists():
            return []
        store = WorldStore(path=path)
        return [(r[0], r[1]) for r in store._conn.execute(  # noqa: SLF001
            "SELECT id, canonical_name FROM entities WHERE type = 'person' AND canonical_name NOT LIKE '+%'")]
    except Exception:  # noqa: BLE001
        return []


def _name_index(people: list[tuple[str, str]]) -> dict[str, tuple[str, str]]:
    """Full names, plus first names that belong to exactly one contact ("🎂 Isabelle")."""
    from collections import defaultdict

    full: dict[str, tuple[str, str]] = {}
    firsts: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for eid, name in people:
        key = name.lower().strip()
        if " " in key:
            full[key] = (eid, name)
        first = key.split()[0] if key else ""
        if len(first) >= 3:
            firsts[first].add((eid, name))
    for first, owners in firsts.items():
        if len(owners) == 1:
            full.setdefault(first, next(iter(owners)))
    return full


def _name_in_title(title: str, names: dict[str, tuple[str, str]]) -> tuple[str, str] | None:
    import re

    low = title.lower()
    for key in sorted(names, key=len, reverse=True):
        if re.search(rf"(?<!\w){re.escape(key)}(?!\w)", low):
            return names[key]
    return None


def _owner_emails() -> set[str]:
    raw = os.environ.get("RECALL_OWNER_EMAILS", "") or os.environ.get("OWNER_EMAILS", "")
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def _likely_owner(settings: Any, now: datetime) -> set[str]:
    """The account owner is on (nearly) every invite: anyone on 60%+ of the last year's events."""
    from collections import Counter

    from recall.data.queries import all_events_in_window

    try:
        from recall.data.query_layer import get_query_layer

        events = all_events_in_window(source_tag="calendar_owner", days_back=365, days_forward=30, now=now, settings=settings,
                                      layer=get_query_layer(settings))
    except Exception:  # noqa: BLE001
        return set()
    invites = [ev for ev in events if ev.attendees]
    seen = Counter(a.lower() for ev in invites for a in set(ev.attendees))
    if len(invites) < 3:
        return set()
    top = seen.most_common(1)
    return {top[0][0]} if top and top[0][1] >= 0.6 * len(invites) else set()


def _parse(raw: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
