"""iMessage connector — macOS chat.db (copied read-only) into recall rows."""

from __future__ import annotations

import hashlib
import re
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from recall.config import Settings, get_settings
from recall.connectors.schema import ConnectorResult
from recall.tracing import Tracer

_APPLE_EPOCH = 978307200
_CHAT_DB = Path.home() / "Library/Messages/chat.db"


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{digest}"


def decode_attributed_body(blob: bytes | bytearray | memoryview | None) -> str:
    """Extract UTF-8 text after the ``NSString`` marker, skipping a length prefix.

    When ``message.text`` is NULL, iMessage stores the body in ``attributedBody``.
    Pure helper for tests — pass hand-made sample blobs.
    """
    if blob is None:
        return ""
    data = bytes(blob)
    marker = b"NSString"
    idx = data.find(marker)
    if idx < 0:
        return ""
    rest = data[idx + len(marker) :]
    # Skip typedstream class / tag bytes commonly present after the marker.
    i = 0
    while i < len(rest) and rest[i] in {
        0x00,
        0x01,
        0x02,
        0x03,
        0x04,
        0x05,
        0x84,
        0x86,
        0x94,
        0x95,
        0x96,
        0x2B,
        0x63,
    }:
        i += 1
    if i >= len(rest):
        return ""
    length = rest[i]
    i += 1
    # One-byte length for short strings; if length looks like a tag, try next byte.
    if length > 0x80 and i < len(rest):
        length = rest[i]
        i += 1
    raw = rest[i : i + length]
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", errors="replace")
    return "".join(ch for ch in text if ch.isprintable() or ch in "\n\t\r ")


def _apple_ts_to_utc(raw: int | float | None) -> str:
    """Convert Messages nanosecond Apple timestamps to UTC ISO-Z."""
    if raw is None:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    value = float(raw)
    # Modern chat.db stores nanoseconds since Apple epoch.
    seconds = value / 1e9 + _APPLE_EPOCH if value > 1e11 else value + _APPLE_EPOCH
    return datetime.fromtimestamp(seconds, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _normalize_handle(raw: str) -> tuple[str, str]:
    """Return (kind, value) for an iMessage handle id."""
    value = raw.strip()
    if "@" in value and not value.startswith("chat"):
        return "email", value.lower()
    digits = re.sub(r"\D", "", value)
    if digits and len(digits) >= 8:
        phone = value if value.startswith("+") else f"+{digits}"
        return "imessage", phone
    return "imessage", value


def fetch_imessage(
    settings: Settings | None = None,
    tracer: Tracer | None = None,
) -> ConnectorResult:
    """Copy chat.db (+ wal/shm), read recent 1:1 / small-group messages, or skip."""
    _ = settings if settings is not None else get_settings()
    active_tracer = tracer if tracer is not None else Tracer.current()
    result = ConnectorResult(name="imessage")

    if sys.platform != "darwin":
        result.skipped = "skipped: iMessage requires macOS"
        if active_tracer is not None:
            active_tracer.event(
                "connector",
                "skipped",
                {"connector": "imessage", "reason": result.skipped},
            )
        return result

    if not _CHAT_DB.is_file():
        result.skipped = "skipped: chat.db not found"
        if active_tracer is not None:
            active_tracer.event(
                "connector",
                "skipped",
                {"connector": "imessage", "reason": result.skipped},
            )
        return result

    try:
        with tempfile.TemporaryDirectory(prefix="recall-imessage-") as tmp:
            tmp_dir = Path(tmp)
            for name in ("chat.db", "chat.db-wal", "chat.db-shm"):
                src = _CHAT_DB.parent / name
                if src.is_file():
                    shutil.copy2(src, tmp_dir / name)
            db_copy = tmp_dir / "chat.db"
            conn = sqlite3.connect(f"file:{db_copy}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            try:
                cutoff = datetime.now(timezone.utc) - timedelta(days=120)
                cutoff_raw = int((cutoff.timestamp() - _APPLE_EPOCH) * 1e9)
                rows = conn.execute(
                    """
                    SELECT
                        m.ROWID AS message_id,
                        m.text AS text,
                        m.attributedBody AS attributed_body,
                        m.is_from_me AS is_from_me,
                        m.date AS msg_date,
                        h.id AS handle_id,
                        c.chat_identifier AS chat_identifier,
                        c.ROWID AS chat_rowid,
                        (
                            SELECT COUNT(*)
                            FROM chat_handle_join chj
                            WHERE chj.chat_id = c.ROWID
                        ) AS participant_count
                    FROM message AS m
                    JOIN chat_message_join AS cmj ON cmj.message_id = m.ROWID
                    JOIN chat AS c ON c.ROWID = cmj.chat_id
                    LEFT JOIN handle AS h ON h.ROWID = m.handle_id
                    WHERE m.date >= ?
                      AND (
                            SELECT COUNT(*)
                            FROM chat_handle_join chj2
                            WHERE chj2.chat_id = c.ROWID
                          ) <= 6
                    ORDER BY m.date DESC
                    """,
                    (cutoff_raw,),
                ).fetchall()
            finally:
                conn.close()

        handles: dict[str, dict[str, Any]] = {}
        interactions: list[dict[str, Any]] = []
        for row in rows:
            text = str(row["text"] or "").strip()
            if not text:
                text = decode_attributed_body(row["attributed_body"]).strip()
            if not text:
                continue
            handle_raw = str(row["handle_id"] or row["chat_identifier"] or "")
            if not handle_raw:
                continue
            kind, value = _normalize_handle(handle_raw)
            direction = "out" if int(row["is_from_me"] or 0) == 1 else "in"
            ts_utc = _apple_ts_to_utc(row["msg_date"])
            chat_key = str(row["chat_identifier"] or row["chat_rowid"])
            handles[_stable_id("h", kind, value)] = {
                "handle_id": _stable_id("h", kind, value),
                "kind": kind,
                "value": value,
                "display_name": value,
                "source": "imessage",
            }
            msg_id = str(row["message_id"])
            interactions.append(
                {
                    "interaction_id": _stable_id("i", "imessage", msg_id),
                    "source": "imessage",
                    "thread_id": _stable_id("th", "imessage", chat_key),
                    "handle_value": value,
                    "direction": direction,
                    "ts_utc": ts_utc,
                    "subject": "",
                    "text": text,
                    "source_ref": f"imessage:{msg_id}",
                }
            )

        result.handles = list(handles.values())
        result.interactions = interactions
        if active_tracer is not None:
            active_tracer.event(
                "connector",
                "ingest",
                {"connector": "imessage", **result.counts()},
            )
        return result
    except Exception as exc:  # noqa: BLE001 - never crash the whole ingest
        result.skipped = f"skipped: {type(exc).__name__}"
        if active_tracer is not None:
            active_tracer.event(
                "connector",
                "skipped",
                {"connector": "imessage", "reason": result.skipped},
            )
        return result
