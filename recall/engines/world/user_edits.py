"""Contact edits made by the user in the dashboard, kept across re-ingests.

Ingest rebuilds person entities from the sources, so an edit can't live only on
the entity row. Each edit is stored against the contact's *handles* (phones,
emails, names) and re-applied after every ingest:

    field edits   canonical_name / org / title / city / notes
    alias edits   add or remove a handle/name shown on the card
    merges        "these two contacts are the same person"
    splits        "these two are different people" (blocks future auto-merges)
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

EDITABLE_FIELDS = ("canonical_name", "org", "title", "city", "notes")

_DDL = """
CREATE TABLE IF NOT EXISTS user_edits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,            -- field | alias_add | alias_remove | merge | split
    anchors_json TEXT NOT NULL,    -- handles identifying the contact (any one matches)
    other_anchors_json TEXT NOT NULL DEFAULT '[]',
    field TEXT NOT NULL DEFAULT '',
    value TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.executescript(_DDL)


def anchors_for(conn: sqlite3.Connection, entity_id: str) -> list[str]:
    """Handles + names that identify an entity across re-ingests."""
    row = conn.execute("SELECT canonical_name, aliases_json FROM entities WHERE id = ?", (entity_id,)).fetchone()
    if row is None:
        return []
    values = [row[0], *json.loads(row[1] or "[]")]
    return sorted({str(v).strip().lower() for v in values if str(v).strip()})


def _find_entity(conn: sqlite3.Connection, anchors: list[str]) -> str | None:
    """Best entity for a set of anchors: prefer handle matches over name matches."""
    anchors_l = {a.lower() for a in anchors}
    handles = {a for a in anchors_l if "@" in a or a.startswith("+")}
    best: tuple[int, str] | None = None
    for row in conn.execute("SELECT id, canonical_name, aliases_json FROM entities WHERE type = 'person'"):
        vals = {str(v).strip().lower() for v in [row[1], *json.loads(row[2] or "[]")]}
        hit = len(vals & handles) * 10 + len(vals & (anchors_l - handles))
        if hit and (best is None or hit > best[0]):
            best = (hit, row[0])
    return best[1] if best else None


def record_edit(conn: sqlite3.Connection, *, kind: str, entity_id: str, field: str = "", value: str = "",
                other_entity_id: str | None = None) -> None:
    ensure_table(conn)
    other = anchors_for(conn, other_entity_id) if other_entity_id else []
    conn.execute(
        "INSERT INTO user_edits(kind, anchors_json, other_anchors_json, field, value, created_at) VALUES (?,?,?,?,?,?)",
        (kind, json.dumps(anchors_for(conn, entity_id)), json.dumps(other), field, value, _now()),
    )
    conn.commit()


def apply_field(conn: sqlite3.Connection, entity_id: str, field: str, value: str) -> None:
    """Write one edited field onto the entity row (canonical_name or an attribute)."""
    if field == "canonical_name":
        if value.strip():
            conn.execute("UPDATE entities SET canonical_name = ? WHERE id = ?", (value.strip(), entity_id))
        return
    row = conn.execute("SELECT attributes_json FROM entities WHERE id = ?", (entity_id,)).fetchone()
    if row is None:
        return
    attrs = json.loads(row[0] or "{}")
    if value.strip():
        attrs[field] = value.strip()
    else:
        attrs.pop(field, None)
    attrs[f"{field}_source"] = "you"
    conn.execute("UPDATE entities SET attributes_json = ? WHERE id = ?", (json.dumps(attrs, ensure_ascii=False), entity_id))


def apply_alias(conn: sqlite3.Connection, entity_id: str, alias: str, *, add: bool) -> None:
    row = conn.execute("SELECT aliases_json FROM entities WHERE id = ?", (entity_id,)).fetchone()
    if row is None:
        return
    aliases = [a for a in json.loads(row[0] or "[]") if a.lower() != alias.lower()]
    if add:
        aliases.append(alias)
    conn.execute("UPDATE entities SET aliases_json = ? WHERE id = ?", (json.dumps(aliases, ensure_ascii=False), entity_id))


def forced_pairs(conn: sqlite3.Connection) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """(merge pairs, split pairs) as current entity ids, for the name-merge pass."""
    ensure_table(conn)
    merges, splits = [], []
    for kind, a_json, b_json in conn.execute("SELECT kind, anchors_json, other_anchors_json FROM user_edits WHERE kind IN ('merge','split') ORDER BY id"):
        a, b = _find_entity(conn, json.loads(a_json)), _find_entity(conn, json.loads(b_json))
        if a and b and a != b:
            (merges if kind == "merge" else splits).append((a, b))
    return merges, splits


def reapply(conn: sqlite3.Connection) -> int:
    """Re-apply field/alias edits after an ingest rebuilt the entities. Returns edits applied."""
    ensure_table(conn)
    applied = 0
    for kind, a_json, field, value in conn.execute("SELECT kind, anchors_json, field, value FROM user_edits WHERE kind IN ('field','alias_add','alias_remove') ORDER BY id").fetchall():
        eid = _find_entity(conn, json.loads(a_json))
        if not eid:
            continue
        if kind == "field":
            apply_field(conn, eid, field, value)
        else:
            apply_alias(conn, eid, value, add=kind == "alias_add")
        applied += 1
    conn.commit()
    return applied


def person_card(conn: sqlite3.Connection, entity_id: str) -> dict[str, Any] | None:
    """Everything a contact card shows, from the world DB."""
    row = conn.execute(
        "SELECT id, canonical_name, aliases_json, attributes_json, first_seen_at, last_seen_at, mention_count, confidence "
        "FROM entities WHERE id = ? AND type = 'person'", (entity_id,)).fetchone()
    if row is None:
        return None
    aliases = json.loads(row[2] or "[]")
    attrs = json.loads(row[3] or "{}")
    emails = sorted({a for a in aliases if "@" in a and not a.endswith("@s.whatsapp.net")})
    phones = sorted({a for a in aliases if a.startswith("+")} | {"+" + a.split("@")[0] for a in aliases if a.endswith("@s.whatsapp.net")})
    names = [a for a in aliases if a not in emails and a not in phones and not a.endswith("@s.whatsapp.net") and a != row[1]]
    rel = conn.execute("SELECT label, confidence FROM relationship_assessments WHERE entity_id = ? ORDER BY assessed_at DESC LIMIT 1", (entity_id,)).fetchone()
    sources = sorted({r[0].split(":", 1)[0] for r in conn.execute("SELECT DISTINCT source_ref FROM observations WHERE entity_id = ?", (entity_id,)) if r[0]})
    merged = 0
    if conn.execute("SELECT name FROM sqlite_master WHERE name = 'merge_decisions'").fetchone():
        merged = conn.execute("SELECT COUNT(*) FROM merge_decisions WHERE merged_into = ? AND decision = 'merged'", (entity_id,)).fetchone()[0]
    return {
        "entity_id": row[0], "name": row[1], "other_names": sorted(set(names))[:8], "emails": emails, "phones": phones,
        "org": attrs.get("org", ""), "title": attrs.get("title", ""), "city": attrs.get("city", ""), "notes": attrs.get("notes", ""),
        "edited_fields": sorted(k[:-7] for k, v in attrs.items() if k.endswith("_source") and v == "you"),
        "relationship": rel[0] if rel else "", "first_seen_at": row[4], "last_seen_at": row[5],
        "mentions": int(row[6] or 0), "confidence": float(row[7] or 0), "sources": [s for s in sources if s not in ("", "user")],
        "merged_from": int(merged),
    }
