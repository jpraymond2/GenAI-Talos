"""Extract iMessage/SMS rows from a copied chat.db (reuses connector helpers)."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from recall.connectors.imessage import (
    _APPLE_EPOCH,
    _apple_ts_to_utc,
    _normalize_handle,
    decode_attributed_body,
)
from recall.mac_agent.copy_db import copied_sqlite
from recall.mac_agent.paths import chat_db_path
from recall.mac_agent.phones import normalize_e164


def extract_imessage(
    *,
    after_rowid: int = 0,
    backfill_days: int = 365,
    max_group_size: int = 15,
    excluded_chats: list[str] | None = None,
    default_region: str = "CA",
    limit: int | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int, int]:
    """Return (messages, handles, max_rowid_seen, skipped_large_groups)."""
    excluded = {c.lower() for c in (excluded_chats or [])}
    db = chat_db_path()
    if not db.is_file():
        return [], [], after_rowid, 0

    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, backfill_days))
    cutoff_raw = int((cutoff.timestamp() - _APPLE_EPOCH) * 1e9)

    with copied_sqlite(db, prefix="recall-imessage-") as copy:
        conn = sqlite3.connect(f"file:{copy}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            sql = """
                SELECT
                    m.ROWID AS rowid,
                    m.guid AS guid,
                    m.text AS text,
                    m.attributedBody AS attributed_body,
                    m.is_from_me AS is_from_me,
                    m.date AS msg_date,
                    m.service AS service,
                    h.id AS handle_id,
                    c.guid AS chat_guid,
                    c.chat_identifier AS chat_identifier,
                    c.service_name AS chat_service,
                    c.display_name AS chat_display_name,
                    c.ROWID AS chat_rowid
                FROM message AS m
                JOIN chat_message_join AS cmj ON cmj.message_id = m.ROWID
                JOIN chat AS c ON c.ROWID = cmj.chat_id
                LEFT JOIN handle AS h ON h.ROWID = m.handle_id
                WHERE m.ROWID > ?
                  AND m.date >= ?
                ORDER BY m.ROWID ASC
                """
            params: list[Any] = [after_rowid, cutoff_raw]
            if limit is not None:
                sql += " LIMIT ?"
                params.append(int(limit))
            rows = conn.execute(sql, params).fetchall()

            chat_ids = {int(r["chat_rowid"]) for r in rows}
            participants: dict[int, list[str]] = {cid: [] for cid in chat_ids}
            if chat_ids:
                placeholders = ",".join("?" for _ in chat_ids)
                part_rows = conn.execute(
                    f"""
                    SELECT chj.chat_id AS chat_id, h.id AS handle_id
                    FROM chat_handle_join AS chj
                    JOIN handle AS h ON h.ROWID = chj.handle_id
                    WHERE chj.chat_id IN ({placeholders})
                    """,
                    tuple(chat_ids),
                ).fetchall()
                for prow in part_rows:
                    participants[int(prow["chat_id"])].append(str(prow["handle_id"] or ""))
        finally:
            conn.close()

    messages: list[dict[str, Any]] = []
    handles: dict[str, dict[str, Any]] = {}
    max_rowid = after_rowid
    skipped_large = 0

    for row in rows:
        rowid = int(row["rowid"] or 0)
        max_rowid = max(max_rowid, rowid)
        chat_guid = str(row["chat_guid"] or row["chat_identifier"] or "")
        if chat_guid.lower() and chat_guid.lower() in excluded:
            continue

        chat_rowid = int(row["chat_rowid"] or 0)
        group_raw = [p for p in participants.get(chat_rowid, []) if p]
        if len(group_raw) > max_group_size:
            skipped_large += 1
            continue

        text = str(row["text"] or "").strip()
        if not text:
            text = decode_attributed_body(row["attributed_body"]).strip()
        if not text or _still_garbled(text):
            continue

        handle_raw = str(row["handle_id"] or "")
        kind, value = _normalize_handle(handle_raw) if handle_raw else ("imessage", "")
        if kind in {"imessage", "phone"} and value:
            e164 = normalize_e164(value, default_region=default_region)
            if e164:
                value = e164
                kind = "phone"

        if not value and group_raw:
            kind, value = _normalize_handle(group_raw[0])
            e164 = normalize_e164(value, default_region=default_region)
            if e164:
                value = e164
                kind = "phone"

        if value:
            handles[value] = {
                "handle_id": value,
                "kind": kind if kind != "imessage" else "phone",
                "value": value,
                "display_name": value,
                "source": "imessage",
            }

        norm_parts: list[str] = []
        for raw in group_raw:
            _k, v = _normalize_handle(raw)
            e164 = normalize_e164(v, default_region=default_region)
            norm_parts.append(e164 or v)

        is_group = len(norm_parts) > 1
        messages.append(
            {
                "guid": str(row["guid"] or f"imessage-row-{rowid}"),
                "rowid": rowid,
                "text": text,
                "ts_utc": _apple_ts_to_utc(row["msg_date"]),
                "is_from_me": bool(int(row["is_from_me"] or 0)),
                "handle": value,
                "service": str(row["service"] or row["chat_service"] or "iMessage"),
                "chat_guid": chat_guid,
                "chat_display_name": str(row["chat_display_name"] or ""),
                "is_group": is_group,
                "group_participants": norm_parts,
                "source": "imessage",
            }
        )

    return messages, list(handles.values()), max_rowid, skipped_large


def _still_garbled(text: str) -> bool:
    lower = text.lower()
    return any(
        m in lower
        for m in (
            "nsdictionary",
            "nsnumber",
            "__kimmessagepartattributename",
            "nsmutable",
        )
    )
