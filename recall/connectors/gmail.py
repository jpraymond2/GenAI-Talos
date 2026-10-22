"""Gmail connector — maps message metadata into recall handles + interactions."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from email.utils import parseaddr, parsedate_to_datetime
from typing import Any

from recall.config import Settings, get_settings
from recall.connectors.schema import ConnectorResult
from recall.tracing import Tracer

_EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
_GMAIL_QUERY = "newer_than:120d -category:promotions -category:social"
_MAX_MESSAGES = 800
_BATCH_SIZE = 50
_METADATA_HEADERS = ["From", "To", "Cc", "Subject", "Date"]


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{digest}"


def _parse_addresses(header_value: str) -> list[tuple[str, str]]:
    """Return (display_name, email) pairs from a From/To/Cc header."""
    if not header_value:
        return []
    found: list[tuple[str, str]] = []
    for chunk in header_value.split(","):
        name, addr = parseaddr(chunk.strip())
        addr = (addr or "").strip().lower()
        if not addr and chunk.strip():
            match = _EMAIL_RE.search(chunk)
            if match:
                addr = match.group(0).lower()
        if not addr:
            continue
        found.append(((name or "").strip(), addr))
    return found


def _header_map(message: dict[str, Any]) -> dict[str, str]:
    headers = (message.get("payload") or {}).get("headers") or []
    out: dict[str, str] = {}
    for item in headers:
        name = str(item.get("name") or "")
        if name:
            out[name.lower()] = str(item.get("value") or "")
    return out


def _parse_date(value: str) -> str:
    if not value:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError, IndexError):
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def map_gmail_message(
    message: dict[str, Any],
    *,
    user_email: str,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Map one Gmail metadata message to handles + an interaction row.

    Pure helper for tests — no network.
    """
    headers = _header_map(message)
    from_list = _parse_addresses(headers.get("from", ""))
    to_list = _parse_addresses(headers.get("to", ""))
    cc_list = _parse_addresses(headers.get("cc", ""))
    subject = headers.get("subject", "")
    ts_utc = _parse_date(headers.get("date", ""))
    snippet = str(message.get("snippet") or "")
    msg_id = str(message.get("id") or "")
    thread_id = str(message.get("threadId") or msg_id)
    user = user_email.strip().lower()

    if not msg_id or not from_list:
        return [], None

    from_name, from_addr = from_list[0]
    direction = "out" if from_addr == user else "in"
    if direction == "out":
        counterparties = [p for p in to_list + cc_list if p[1] != user]
        handle_value = counterparties[0][1] if counterparties else (to_list[0][1] if to_list else from_addr)
        display = counterparties[0][0] if counterparties else ""
    else:
        handle_value = from_addr
        display = from_name

    handles: list[dict[str, Any]] = []
    seen: set[str] = set()
    for name, addr in from_list + to_list + cc_list:
        if addr == user or addr in seen:
            continue
        seen.add(addr)
        handles.append(
            {
                "handle_id": _stable_id("h", "email", addr),
                "kind": "email",
                "value": addr,
                "display_name": name or addr,
                "source": "gmail",
            }
        )

    interaction = {
        "interaction_id": _stable_id("i", "gmail", msg_id),
        "source": "gmail",
        "thread_id": thread_id,
        "handle_value": handle_value,
        "direction": direction,
        "ts_utc": ts_utc,
        "subject": subject,
        "text": snippet,
        "source_ref": f"gmail:{msg_id}",
    }
    if display and handle_value:
        # Ensure the counterparty handle exists even if only in From.
        if handle_value not in seen and handle_value != user:
            handles.append(
                {
                    "handle_id": _stable_id("h", "email", handle_value),
                    "kind": "email",
                    "value": handle_value,
                    "display_name": display or handle_value,
                    "source": "gmail",
                }
            )
    return handles, interaction


def fetch_gmail(
    settings: Settings | None = None,
    tracer: Tracer | None = None,
) -> ConnectorResult:
    """Pull recent Gmail metadata into recall rows, or skip with a trace reason."""
    active = settings if settings is not None else get_settings()
    active_tracer = tracer if tracer is not None else Tracer.current()
    result = ConnectorResult(name="gmail")

    try:
        from googleapiclient.discovery import build
        from googleapiclient.http import BatchHttpRequest

        from recall.connectors.google_auth import load_google_credentials

        creds = load_google_credentials(active)
        service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        profile = service.users().getProfile(userId="me").execute()
        user_email = str(profile.get("emailAddress") or "").lower()
        if not user_email:
            raise RuntimeError("Gmail profile missing emailAddress")

        message_refs: list[dict[str, str]] = []
        request = (
            service.users()
            .messages()
            .list(userId="me", q=_GMAIL_QUERY, maxResults=min(500, _MAX_MESSAGES))
        )
        while request is not None and len(message_refs) < _MAX_MESSAGES:
            page = request.execute()
            message_refs.extend(page.get("messages") or [])
            if len(message_refs) >= _MAX_MESSAGES:
                break
            request = service.users().messages().list_next(request, page)

        message_refs = message_refs[:_MAX_MESSAGES]
        fetched: list[dict[str, Any]] = []

        def _collect(request_id: str, response: dict[str, Any], exception: Exception | None) -> None:
            _ = request_id
            if exception is not None:
                return
            if response:
                fetched.append(response)

        for start in range(0, len(message_refs), _BATCH_SIZE):
            chunk = message_refs[start : start + _BATCH_SIZE]
            batch: BatchHttpRequest = service.new_batch_http_request(callback=_collect)
            for ref in chunk:
                batch.add(
                    service.users()
                    .messages()
                    .get(
                        userId="me",
                        id=ref["id"],
                        format="metadata",
                        metadataHeaders=_METADATA_HEADERS,
                    )
                )
            batch.execute()

        handle_by_id: dict[str, dict[str, Any]] = {}
        interactions: list[dict[str, Any]] = []
        for message in fetched:
            handles, interaction = map_gmail_message(message, user_email=user_email)
            for handle in handles:
                handle_by_id[handle["handle_id"]] = handle
            if interaction is not None:
                interactions.append(interaction)

        result.handles = list(handle_by_id.values())
        result.interactions = interactions
        if active_tracer is not None:
            active_tracer.event("connector", "ingest", {"connector": "gmail", **result.counts()})
        return result
    except Exception as exc:  # noqa: BLE001 - never crash the whole ingest
        reason = _skip_reason(exc)
        result.skipped = reason
        if active_tracer is not None:
            active_tracer.event(
                "connector",
                "skipped",
                {"connector": "gmail", "reason": reason},
            )
        return result


def _skip_reason(exc: Exception) -> str:
    text = str(exc)
    # Never echo tokens or credential file contents.
    if "credentials.json" in text or isinstance(exc, FileNotFoundError):
        return "skipped: missing Google OAuth credentials"
    if "token" in text.lower() and "refresh" in text.lower():
        return "skipped: Google OAuth token refresh failed"
    return f"skipped: {type(exc).__name__}"
