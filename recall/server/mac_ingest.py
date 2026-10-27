"""Postgres raw-table upserts for Mac agent uploads + debounced world rebuild hook.

We keep Mac rows in ``raw_*`` tables (what Zetaris will register). As the smaller
bridge to the existing world pipeline, each upload also merges a parquet snapshot
under ``LIVE_DATA_DIR`` so ``ingest --world`` / DuckDB can still see the data.
World identity/relationship scoring is triggered in a background task, debounced
to at most once every 5 minutes.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

from recall.config import Settings, get_settings
from recall.connectors.ingest import merge_results, write_live_parquet
from recall.connectors.schema import ConnectorResult

logger = logging.getLogger(__name__)

_DDL = """
CREATE TABLE IF NOT EXISTS raw_imessage_messages (
    guid TEXT PRIMARY KEY,
    rowid BIGINT,
    text TEXT,
    ts_utc TEXT,
    is_from_me BOOLEAN,
    handle TEXT,
    service TEXT,
    chat_guid TEXT,
    chat_display_name TEXT,
    is_group BOOLEAN,
    group_participants JSONB,
    contact_name TEXT,
    source TEXT,
    device_id TEXT,
    ingested_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS raw_whatsapp_messages (
    guid TEXT PRIMARY KEY,
    z_pk BIGINT,
    text TEXT,
    ts_utc TEXT,
    is_from_me BOOLEAN,
    handle TEXT,
    service TEXT,
    chat_guid TEXT,
    chat_display_name TEXT,
    is_group BOOLEAN,
    group_participants JSONB,
    contact_name TEXT,
    source TEXT,
    device_id TEXT,
    ingested_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS raw_handles (
    handle_id TEXT PRIMARY KEY,
    kind TEXT,
    value TEXT,
    display_name TEXT,
    source TEXT,
    device_id TEXT,
    ingested_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS raw_contacts (
    contact_id TEXT PRIMARY KEY,
    name TEXT,
    organization TEXT,
    title TEXT,
    phones JSONB,
    emails JSONB,
    source TEXT,
    device_id TEXT,
    ingested_at TIMESTAMPTZ DEFAULT NOW()
);
"""

_rebuild_lock = threading.Lock()
_last_rebuild_at = 0.0
_REBUILD_DEBOUNCE_SEC = 300.0


def ensure_raw_tables(conn: Any) -> None:
    with conn.cursor() as cur:
        for stmt in _DDL.split(";"):
            text = stmt.strip()
            if text:
                cur.execute(text)
    conn.commit()


def upsert_mac_payload(
    payload: dict[str, Any],
    *,
    settings: Settings | None = None,
) -> dict[str, int]:
    """Upsert raw tables; return per-table upsert counts. Raises if DATABASE_URL missing."""
    active = settings if settings is not None else get_settings()
    dsn = (active.database_url or "").strip()
    if not dsn:
        raise RuntimeError("DATABASE_URL is not configured")

    import psycopg

    device_id = str(payload.get("device_id") or "")
    counts = {
        "raw_imessage_messages": 0,
        "raw_whatsapp_messages": 0,
        "raw_handles": 0,
        "raw_contacts": 0,
    }

    with psycopg.connect(dsn) as conn:
        ensure_raw_tables(conn)
        with conn.cursor() as cur:
            for row in payload.get("imessage_messages") or []:
                cur.execute(
                    """
                    INSERT INTO raw_imessage_messages (
                        guid, rowid, text, ts_utc, is_from_me, handle, service,
                        chat_guid, chat_display_name, is_group, group_participants,
                        contact_name, source, device_id
                    ) VALUES (
                        %(guid)s, %(rowid)s, %(text)s, %(ts_utc)s, %(is_from_me)s,
                        %(handle)s, %(service)s, %(chat_guid)s, %(chat_display_name)s,
                        %(is_group)s, %(group_participants)s::jsonb, %(contact_name)s,
                        %(source)s, %(device_id)s
                    )
                    ON CONFLICT (guid) DO UPDATE SET
                        text = EXCLUDED.text,
                        ts_utc = EXCLUDED.ts_utc,
                        contact_name = EXCLUDED.contact_name,
                        is_group = EXCLUDED.is_group,
                        group_participants = EXCLUDED.group_participants,
                        device_id = EXCLUDED.device_id,
                        ingested_at = NOW()
                    """,
                    {
                        "guid": str(row.get("guid") or ""),
                        "rowid": row.get("rowid"),
                        "text": row.get("text") or "",
                        "ts_utc": row.get("ts_utc") or "",
                        "is_from_me": bool(row.get("is_from_me")),
                        "handle": row.get("handle") or "",
                        "service": row.get("service") or "",
                        "chat_guid": row.get("chat_guid") or "",
                        "chat_display_name": row.get("chat_display_name") or "",
                        "is_group": bool(row.get("is_group")),
                        "group_participants": json.dumps(
                            row.get("group_participants") or []
                        ),
                        "contact_name": row.get("contact_name") or "",
                        "source": row.get("source") or "imessage",
                        "device_id": device_id,
                    },
                )
                counts["raw_imessage_messages"] += 1

            for row in payload.get("whatsapp_messages") or []:
                cur.execute(
                    """
                    INSERT INTO raw_whatsapp_messages (
                        guid, z_pk, text, ts_utc, is_from_me, handle, service,
                        chat_guid, chat_display_name, is_group, group_participants,
                        contact_name, source, device_id
                    ) VALUES (
                        %(guid)s, %(z_pk)s, %(text)s, %(ts_utc)s, %(is_from_me)s,
                        %(handle)s, %(service)s, %(chat_guid)s, %(chat_display_name)s,
                        %(is_group)s, %(group_participants)s::jsonb, %(contact_name)s,
                        %(source)s, %(device_id)s
                    )
                    ON CONFLICT (guid) DO UPDATE SET
                        text = EXCLUDED.text,
                        ts_utc = EXCLUDED.ts_utc,
                        contact_name = EXCLUDED.contact_name,
                        is_group = EXCLUDED.is_group,
                        group_participants = EXCLUDED.group_participants,
                        device_id = EXCLUDED.device_id,
                        ingested_at = NOW()
                    """,
                    {
                        "guid": str(row.get("guid") or ""),
                        "z_pk": row.get("z_pk"),
                        "text": row.get("text") or "",
                        "ts_utc": row.get("ts_utc") or "",
                        "is_from_me": bool(row.get("is_from_me")),
                        "handle": row.get("handle") or "",
                        "service": row.get("service") or "",
                        "chat_guid": row.get("chat_guid") or "",
                        "chat_display_name": row.get("chat_display_name") or "",
                        "is_group": bool(row.get("is_group")),
                        "group_participants": json.dumps(
                            row.get("group_participants") or []
                        ),
                        "contact_name": row.get("contact_name") or "",
                        "source": row.get("source") or "whatsapp",
                        "device_id": device_id,
                    },
                )
                counts["raw_whatsapp_messages"] += 1

            for row in payload.get("handles") or []:
                hid = str(row.get("handle_id") or row.get("value") or "")
                if not hid:
                    continue
                cur.execute(
                    """
                    INSERT INTO raw_handles (
                        handle_id, kind, value, display_name, source, device_id
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (handle_id) DO UPDATE SET
                        display_name = EXCLUDED.display_name,
                        kind = EXCLUDED.kind,
                        value = EXCLUDED.value,
                        device_id = EXCLUDED.device_id,
                        ingested_at = NOW()
                    """,
                    (
                        hid,
                        row.get("kind") or "",
                        row.get("value") or hid,
                        row.get("display_name") or "",
                        row.get("source") or "",
                        device_id,
                    ),
                )
                counts["raw_handles"] += 1

            for row in payload.get("contacts") or []:
                cid = str(row.get("contact_id") or "")
                if not cid:
                    continue
                cur.execute(
                    """
                    INSERT INTO raw_contacts (
                        contact_id, name, organization, title, phones, emails,
                        source, device_id
                    ) VALUES (
                        %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s
                    )
                    ON CONFLICT (contact_id) DO UPDATE SET
                        name = EXCLUDED.name,
                        organization = EXCLUDED.organization,
                        title = EXCLUDED.title,
                        phones = EXCLUDED.phones,
                        emails = EXCLUDED.emails,
                        device_id = EXCLUDED.device_id,
                        ingested_at = NOW()
                    """,
                    (
                        cid,
                        row.get("name") or "",
                        row.get("organization") or "",
                        row.get("title") or "",
                        json.dumps(row.get("phones") or []),
                        json.dumps(row.get("emails") or []),
                        row.get("source") or "macos_contacts",
                        device_id,
                    ),
                )
                counts["raw_contacts"] += 1
        conn.commit()

    _write_live_parquet_bridge(payload, settings=active)
    return counts


def _write_live_parquet_bridge(
    payload: dict[str, Any],
    *,
    settings: Settings,
) -> None:
    """Smaller bridge: map Mac rows into the existing live parquet tables."""
    interactions: list[dict[str, Any]] = []
    for row in list(payload.get("imessage_messages") or []) + list(
        payload.get("whatsapp_messages") or []
    ):
        source = str(row.get("source") or "imessage")
        guid = str(row.get("guid") or "")
        interactions.append(
            {
                "interaction_id": f"{source}:{guid}",
                "source": source,
                "thread_id": str(row.get("chat_guid") or ""),
                "handle_value": str(row.get("handle") or ""),
                "direction": "out" if row.get("is_from_me") else "in",
                "ts_utc": str(row.get("ts_utc") or ""),
                "subject": "",
                "text": str(row.get("text") or ""),
                "source_ref": f"{source}:{guid}",
            }
        )
    contacts = []
    for row in payload.get("contacts") or []:
        contacts.append(
            {
                "contact_id": str(row.get("contact_id") or ""),
                "full_name": str(row.get("name") or ""),
                "org": str(row.get("organization") or ""),
                "title": str(row.get("title") or ""),
                "city": "",
                "emails": json.dumps(row.get("emails") or []),
                "phones": json.dumps(row.get("phones") or []),
                "source": str(row.get("source") or "macos_contacts"),
            }
        )
    handles = []
    for row in payload.get("handles") or []:
        handles.append(
            {
                "handle_id": str(row.get("handle_id") or row.get("value") or ""),
                "kind": str(row.get("kind") or ""),
                "value": str(row.get("value") or ""),
                "display_name": str(row.get("display_name") or ""),
                "source": str(row.get("source") or ""),
            }
        )
    result = ConnectorResult(
        name="mac_agent",
        handles=handles,
        interactions=interactions,
        contacts=contacts,
    )
    live = Path(settings.live_data_dir)
    # Merge with any existing parquet by re-reading is heavy; write this batch merged
    # on top of empty prior for the bridge (world job can also read raw_*).
    tables = merge_results([result])
    try:
        write_live_parquet(tables, live)
    except OSError as exc:
        logger.warning("live parquet bridge skipped: %s", exc)


def schedule_debounced_world_rebuild(settings: Settings | None = None) -> None:
    """Run world ingest at most once per 5 minutes (background thread)."""
    global _last_rebuild_at
    active = settings if settings is not None else get_settings()

    def _worker() -> None:
        global _last_rebuild_at
        with _rebuild_lock:
            now = time.time()
            if now - _last_rebuild_at < _REBUILD_DEBOUNCE_SEC:
                logger.info("mac ingest rebuild debounced")
                return
            _last_rebuild_at = now
        try:
            from recall.engines.world import run_world_ingest, world

            world.reset()
            counts = run_world_ingest(sample=False, settings=active)
            logger.info("mac ingest world rebuild done: %s", counts)
        except Exception:  # noqa: BLE001
            logger.exception("mac ingest world rebuild failed")

    threading.Thread(target=_worker, name="mac-ingest-rebuild", daemon=True).start()
