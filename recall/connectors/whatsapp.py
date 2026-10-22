"""WhatsApp connector — macOS ChatStorage sqlite and Export-chat .txt files."""

from __future__ import annotations

import hashlib
import re
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from recall.config import Settings, get_settings
from recall.connectors.schema import ConnectorResult
from recall.tracing import Tracer

# Apple Cocoa absolute time epoch (seconds between 1970-01-01 and 2001-01-01).
_APPLE_EPOCH = 978307200
_IPHONE_RE = re.compile(
    r"^\[(\d{1,2}/\d{1,2}/\d{2,4}),\s+(\d{1,2}:\d{2}(?::\d{2})?(?:\s*[AaPp][Mm])?)\]\s+"
    r"([^:]+):\s?(.*)$"
)
_ANDROID_RE = re.compile(
    r"^(\d{1,2}/\d{1,2}/\d{2,4}),\s+(\d{1,2}:\d{2}(?::\d{2})?)\s+-\s+"
    r"([^:]+):\s?(.*)$"
)
_SYSTEM_HINTS = (
    "messages and calls are end-to-end encrypted",
    "created group",
    "added you",
    "left",
    "changed the subject",
    "changed this group's icon",
    "security code changed",
    "you're now an admin",
    "missed voice call",
    "missed video call",
)


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{digest}"


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value)


def jid_to_phone(jid: str) -> str:
    """Map ``15145550142@s.whatsapp.net`` (or similar) to ``+15145550142``."""
    local = jid.split("@", 1)[0]
    digits = _digits(local)
    if not digits:
        return jid
    return f"+{digits}"


def _is_media_or_system(text: str, sender: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return True
    if stripped.lower() in {"<media omitted>", "media omitted"}:
        return True
    if "<media omitted>" in stripped.lower():
        return True
    # System lines often have no real "Name: text" or match known phrases.
    lower = stripped.lower()
    if any(hint in lower for hint in _SYSTEM_HINTS):
        return True
    # Android system: "dd/mm/yyyy, hh:mm - Name left" without colon body handled elsewhere.
    if not sender.strip():
        return True
    return False


def _parse_timestamp(date_part: str, time_part: str) -> str:
    """Parse export timestamps into UTC ISO-Z (treat naive as UTC)."""
    time_part = time_part.strip()
    date_part = date_part.strip()
    candidates = [
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%d/%m/%y %H:%M:%S",
        "%d/%m/%y %H:%M",
        "%d/%m/%Y %I:%M:%S %p",
        "%d/%m/%Y %I:%M %p",
        "%d/%m/%y %I:%M:%S %p",
        "%d/%m/%y %I:%M %p",
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y %H:%M",
        "%m/%d/%y %H:%M:%S",
        "%m/%d/%y %H:%M",
    ]
    stamp = f"{date_part} {time_part}"
    for fmt in candidates:
        try:
            dt = datetime.strptime(stamp, fmt).replace(tzinfo=timezone.utc)
            return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            continue
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_whatsapp_export(text: str, *, chat_name: str = "export") -> list[dict[str, Any]]:
    """Parse an iPhone or Android WhatsApp export into interaction-shaped dicts.

    Also returns parallel handle hints via ``_display`` / ``_phone`` keys stripped
    by ``fetch_whatsapp`` when building ConnectorResult. Pure helper for tests.
    """
    rows: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    def flush() -> None:
        nonlocal current
        if current is None:
            return
        body = str(current.get("text") or "").strip()
        sender = str(current.get("_sender") or "")
        if _is_media_or_system(body, sender):
            current = None
            return
        rows.append(current)
        current = None

    for raw_line in text.splitlines():
        line = raw_line.rstrip("\n")
        match = _IPHONE_RE.match(line) or _ANDROID_RE.match(line)
        if match:
            flush()
            date_part, time_part, sender, body = match.groups()
            sender = sender.strip()
            # System lines sometimes match with odd senders; filter on body.
            if _is_media_or_system(body, sender) and not body.strip():
                current = None
                continue
            ts = _parse_timestamp(date_part, time_part)
            phone_hint = jid_to_phone(sender) if "@" in sender else sender
            # Exports use display names, not JIDs — keep name as handle value key.
            handle_value = sender if not sender.startswith("+") else sender
            # Prefer phone-looking senders.
            if re.fullmatch(r"\+?\d[\d\s\-]+", sender.strip()):
                handle_value = jid_to_phone(sender)
            interaction_key = f"{chat_name}|{ts}|{sender}|{body[:40]}"
            current = {
                "interaction_id": _stable_id("i", "wa_export", interaction_key),
                "source": "whatsapp",
                "thread_id": _stable_id("th", "wa_export", chat_name),
                "handle_value": handle_value,
                "direction": "in",
                "ts_utc": ts,
                "subject": "",
                "text": body,
                "source_ref": f"whatsapp:export:{_stable_id('x', interaction_key)}",
                "_sender": sender,
            }
            continue
        # Continuation / system-only line without a timestamp prefix.
        if current is not None:
            if line.strip().lower() in {"<media omitted>", "media omitted"}:
                # Drop the whole message if it becomes media-only later — append then flush-filter.
                current["text"] = f"{current.get('text', '')}\n{line}".strip()
            else:
                current["text"] = f"{current.get('text', '')}\n{line}".rstrip()
        # else: leading system preamble — ignore
    flush()

    # Drop media-only after flush assembly.
    cleaned: list[dict[str, Any]] = []
    for row in rows:
        body = str(row.get("text") or "")
        if body.strip().lower() in {"<media omitted>", "media omitted"}:
            continue
        if "<media omitted>" in body.lower() and len(body.strip()) < 40:
            continue
        cleaned.append({k: v for k, v in row.items() if not k.startswith("_")})
    return cleaned


def _find_whatsapp_sqlite() -> Path | None:
    root = Path.home() / "Library/Group Containers/group.net.whatsapp.WhatsApp.shared"
    if not root.is_dir():
        return None
    for path in root.rglob("*.sqlite"):
        try:
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            try:
                row = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='ZWAMESSAGE'"
                ).fetchone()
            finally:
                conn.close()
            if row:
                return path
        except sqlite3.Error:
            continue
    return None


def _fetch_macos_chatstorage(cutoff: datetime) -> ConnectorResult:
    db_path = _find_whatsapp_sqlite()
    if db_path is None:
        return ConnectorResult(name="whatsapp", skipped="skipped: WhatsApp ChatStorage not found")

    with tempfile.TemporaryDirectory(prefix="recall-wa-") as tmp:
        tmp_db = Path(tmp) / "ChatStorage.sqlite"
        shutil.copy2(db_path, tmp_db)
        conn = sqlite3.connect(f"file:{tmp_db}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            cutoff_apple = cutoff.timestamp() - _APPLE_EPOCH
            rows = conn.execute(
                """
                SELECT
                    m.Z_PK AS msg_pk,
                    m.ZTEXT AS text,
                    m.ZISFROMME AS is_from_me,
                    m.ZMESSAGEDATE AS msg_date,
                    s.ZPARTNERNAME AS partner_name,
                    s.ZCONTACTJID AS contact_jid
                FROM ZWAMESSAGE AS m
                JOIN ZWACHATSESSION AS s ON m.ZCHATSESSION = s.Z_PK
                WHERE m.ZTEXT IS NOT NULL AND trim(m.ZTEXT) != ''
                  AND m.ZMESSAGEDATE >= ?
                ORDER BY m.ZMESSAGEDATE DESC
                """,
                (cutoff_apple,),
            ).fetchall()
        finally:
            conn.close()

    handles: dict[str, dict[str, Any]] = {}
    interactions: list[dict[str, Any]] = []
    for row in rows:
        text = str(row["text"] or "").strip()
        if not text:
            continue
        jid = str(row["contact_jid"] or "")
        phone = jid_to_phone(jid) if jid else ""
        partner = str(row["partner_name"] or phone or jid)
        msg_date = float(row["msg_date"] or 0)
        ts = datetime.fromtimestamp(msg_date + _APPLE_EPOCH, tz=timezone.utc)
        ts_utc = ts.strftime("%Y-%m-%dT%H:%M:%SZ")
        direction = "out" if int(row["is_from_me"] or 0) == 1 else "in"
        handle_value = phone or jid or partner
        if jid and "@s.whatsapp.net" in jid:
            handles[_stable_id("h", "wa_jid", jid)] = {
                "handle_id": _stable_id("h", "wa_jid", jid),
                "kind": "wa_jid",
                "value": jid,
                "display_name": partner,
                "source": "whatsapp",
            }
        if phone:
            handles[_stable_id("h", "phone", phone)] = {
                "handle_id": _stable_id("h", "phone", phone),
                "kind": "phone",
                "value": phone,
                "display_name": partner,
                "source": "whatsapp",
            }
        msg_pk = str(row["msg_pk"])
        interactions.append(
            {
                "interaction_id": _stable_id("i", "whatsapp", msg_pk),
                "source": "whatsapp",
                "thread_id": _stable_id("th", "whatsapp", jid or partner),
                "handle_value": handle_value,
                "direction": direction,
                "ts_utc": ts_utc,
                "subject": "",
                "text": text,
                "source_ref": f"whatsapp:{msg_pk}",
            }
        )

    return ConnectorResult(
        name="whatsapp",
        handles=list(handles.values()),
        interactions=interactions,
    )


def _iter_export_files(directory: Path) -> Iterator[Path]:
    if not directory.is_dir():
        return
    yield from sorted(directory.glob("*.txt"))


def _fetch_exports(directory: Path) -> ConnectorResult:
    handles: dict[str, dict[str, Any]] = {}
    interactions: list[dict[str, Any]] = []
    for path in _iter_export_files(directory):
        text = path.read_text(encoding="utf-8", errors="replace")
        parsed = parse_whatsapp_export(text, chat_name=path.stem)
        for row in parsed:
            handle_value = str(row["handle_value"])
            handles[_stable_id("h", "wa_export", handle_value)] = {
                "handle_id": _stable_id("h", "wa_export", handle_value),
                "kind": "phone",
                "value": handle_value,
                "display_name": handle_value,
                "source": "whatsapp",
            }
            interactions.append(row)
    if not interactions and not directory.is_dir():
        return ConnectorResult(
            name="whatsapp",
            skipped="skipped: no WhatsApp exports directory",
        )
    return ConnectorResult(name="whatsapp", handles=list(handles.values()), interactions=interactions)


def fetch_whatsapp(
    settings: Settings | None = None,
    tracer: Tracer | None = None,
) -> ConnectorResult:
    """Load WhatsApp from macOS ChatStorage and/or export .txt files."""
    active = settings if settings is not None else get_settings()
    active_tracer = tracer if tracer is not None else Tracer.current()
    cutoff = datetime.now(timezone.utc) - timedelta(days=120)
    export_dir = Path(active.live_data_dir) / "whatsapp_exports"

    merged = ConnectorResult(name="whatsapp")
    reasons: list[str] = []

    if sys.platform == "darwin":
        try:
            mac = _fetch_macos_chatstorage(cutoff)
            if mac.skipped:
                reasons.append(mac.skipped)
            else:
                merged.handles.extend(mac.handles)
                merged.interactions.extend(mac.interactions)
        except Exception as exc:  # noqa: BLE001
            reasons.append(f"skipped: macOS WhatsApp error ({type(exc).__name__})")
    else:
        reasons.append("skipped: WhatsApp ChatStorage requires macOS")

    try:
        exports = _fetch_exports(export_dir)
        if exports.skipped:
            reasons.append(exports.skipped)
        else:
            merged.handles.extend(exports.handles)
            merged.interactions.extend(exports.interactions)
    except Exception as exc:  # noqa: BLE001
        reasons.append(f"skipped: WhatsApp export error ({type(exc).__name__})")

    # Dedupe within connector by id before returning.
    merged.handles = list({h["handle_id"]: h for h in merged.handles}.values())
    merged.interactions = list({i["interaction_id"]: i for i in merged.interactions}.values())

    if not merged.handles and not merged.interactions:
        reason = reasons[0] if reasons else "skipped: no WhatsApp data sources"
        merged.skipped = reason if reason.startswith("skipped:") else f"skipped: {reason}"
        if active_tracer is not None:
            active_tracer.event(
                "connector",
                "skipped",
                {"connector": "whatsapp", "reason": merged.skipped},
            )
        return merged

    if active_tracer is not None:
        payload = {"connector": "whatsapp", **merged.counts()}
        if reasons:
            payload["notes"] = reasons
        active_tracer.event("connector", "ingest", payload)
    return merged
