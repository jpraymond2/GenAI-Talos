"""Extract WhatsApp Desktop ChatStorage messages (skip cleanly if missing)."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from recall.connectors.whatsapp import _APPLE_EPOCH, _find_whatsapp_sqlite, jid_to_phone
from recall.mac_agent.copy_db import copied_sqlite
from recall.mac_agent.phones import normalize_e164


def extract_whatsapp(
    *,
    after_zpk: int = 0,
    backfill_days: int = 365,
    max_group_size: int = 15,
    default_region: str = "CA",
    limit: int | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int, int, str | None]:
    """Return (messages, handles, max_zpk, skipped_large_groups, skip_reason)."""
    db_path = _find_whatsapp_sqlite()
    if db_path is None:
        return (
            [],
            [],
            after_zpk,
            0,
            "WhatsApp ChatStorage.sqlite not found (Desktop not installed?)",
        )

    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, backfill_days))
    cutoff_apple = cutoff.timestamp() - _APPLE_EPOCH

    try:
        with copied_sqlite(db_path, prefix="recall-wa-") as copy:
            conn = sqlite3.connect(f"file:{copy}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            try:
                sql = """
                    SELECT
                        m.Z_PK AS z_pk,
                        m.ZTEXT AS text,
                        m.ZISFROMME AS is_from_me,
                        m.ZMESSAGEDATE AS msg_date,
                        m.ZSTANZAID AS stanza_id,
                        s.ZPARTNERNAME AS partner_name,
                        s.ZCONTACTJID AS contact_jid,
                        s.Z_PK AS session_pk
                    FROM ZWAMESSAGE AS m
                    JOIN ZWACHATSESSION AS s ON m.ZCHATSESSION = s.Z_PK
                    WHERE m.Z_PK > ?
                      AND m.ZMESSAGEDATE >= ?
                      AND m.ZTEXT IS NOT NULL AND trim(m.ZTEXT) != ''
                    ORDER BY m.Z_PK ASC
                    """
                params: list[Any] = [after_zpk, cutoff_apple]
                if limit is not None:
                    sql += " LIMIT ?"
                    params.append(int(limit))
                rows = conn.execute(sql, params).fetchall()

                session_ids = {int(r["session_pk"]) for r in rows}
                member_counts: dict[int, int] = {}
                member_lists: dict[int, list[str]] = {}
                if session_ids:
                    placeholders = ",".join("?" for _ in session_ids)
                    try:
                        mrows = conn.execute(
                            f"""
                            SELECT ZCHATSESSION AS sid, ZMEMBERJID AS jid,
                                   ZCONTACTNAME AS name, ZISACTIVE AS active
                            FROM ZWAGROUPMEMBER
                            WHERE ZCHATSESSION IN ({placeholders})
                            """,
                            tuple(session_ids),
                        ).fetchall()
                        for mr in mrows:
                            if int(mr["active"] or 0) != 1:
                                continue
                            sid = int(mr["sid"])
                            member_counts[sid] = member_counts.get(sid, 0) + 1
                            label = str(mr["name"] or mr["jid"] or "").strip()
                            if label:
                                member_lists.setdefault(sid, []).append(label)
                    except sqlite3.Error:
                        member_counts = {}
            finally:
                conn.close()
    except OSError as exc:
        return [], [], after_zpk, 0, f"WhatsApp read error: {exc}"
    except sqlite3.Error as exc:
        return [], [], after_zpk, 0, f"WhatsApp sqlite error: {exc}"

    messages: list[dict[str, Any]] = []
    handles: dict[str, dict[str, Any]] = {}
    max_zpk = after_zpk
    skipped_large = 0

    for row in rows:
        z_pk = int(row["z_pk"] or 0)
        max_zpk = max(max_zpk, z_pk)
        text = str(row["text"] or "").strip()
        if not text:
            continue
        jid = str(row["contact_jid"] or "")
        is_group_jid = jid.endswith("@g.us")
        session_pk = int(row["session_pk"] or 0)
        n_members = member_counts.get(session_pk, 0)
        if is_group_jid and n_members > max_group_size:
            skipped_large += 1
            continue
        # Unknown member count but clearly a huge LID-style group id — treat as group;
        # if we have no roster, keep the message but mark is_group.
        partner = str(row["partner_name"] or "").strip()
        phone = ""
        handle = jid or partner
        kind = "wa_jid"
        if jid.endswith("@s.whatsapp.net"):
            phone = normalize_e164(jid_to_phone(jid), default_region=default_region)
            handle = phone or jid
            kind = "phone" if phone else "wa_jid"
        elif is_group_jid:
            handle = jid
            kind = "wa_group"

        msg_date = float(row["msg_date"] or 0)
        ts = datetime.fromtimestamp(msg_date + _APPLE_EPOCH, tz=timezone.utc)
        stanza = str(row["stanza_id"] or "")
        guid = stanza if stanza else f"wa-{z_pk}"
        participants = member_lists.get(session_pk) or ([handle] if handle else [])
        is_group = is_group_jid or n_members > 1

        if handle and kind != "wa_group":
            handles[handle] = {
                "handle_id": handle,
                "kind": kind,
                "value": handle,
                "display_name": partner or handle,
                "source": "whatsapp",
            }

        messages.append(
            {
                "guid": guid,
                "z_pk": z_pk,
                "text": text,
                "ts_utc": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "is_from_me": bool(int(row["is_from_me"] or 0)),
                "handle": handle,
                "service": "WhatsApp",
                "chat_guid": jid or str(session_pk),
                "chat_display_name": partner,
                "is_group": is_group,
                "group_participants": participants,
                "source": "whatsapp",
                # Prefer partner name for groups (not the numeric g.us local-part).
                "contact_name_hint": partner or None,
            }
        )

    return messages, list(handles.values()), max_zpk, skipped_large, None
