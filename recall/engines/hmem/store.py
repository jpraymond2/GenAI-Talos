"""SQLite persistence and lifecycle ops for H-MEM."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from recall.config import Settings, get_settings
from recall.engines.hmem.models import Memory

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    type TEXT NOT NULL,
    layer TEXT NOT NULL,
    entity_ids_json TEXT NOT NULL DEFAULT '[]',
    tags_json TEXT NOT NULL DEFAULT '[]',
    embedding_json TEXT NOT NULL DEFAULT '[]',
    confidence REAL NOT NULL DEFAULT 0.7,
    source TEXT NOT NULL DEFAULT 'user_note',
    timestamp TEXT NOT NULL DEFAULT '',
    last_accessed TEXT NOT NULL DEFAULT '',
    access_count INTEGER NOT NULL DEFAULT 0,
    supersedes TEXT NOT NULL DEFAULT '',
    derived_from_json TEXT NOT NULL DEFAULT '[]',
    agent_id TEXT NOT NULL DEFAULT 'hmem',
    run_id TEXT NOT NULL DEFAULT '',
    attribute_key TEXT NOT NULL DEFAULT '',
    attribute_value TEXT NOT NULL DEFAULT '',
    superseded_by TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_memories_layer ON memories(layer);
CREATE INDEX IF NOT EXISTS idx_memories_attr ON memories(attribute_key, attribute_value);
"""


def hmem_db_path(settings: Settings | None = None) -> Path:
    """Return data/hmem_sample.db in SAMPLE_MODE, else data/hmem.db."""
    active = settings if settings is not None else get_settings()
    return Path("data/hmem_sample.db" if active.sample_mode else "data/hmem.db")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_ts(value: str) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return datetime.now(timezone.utc)


def new_memory_id(*parts: str) -> str:
    """Stable-ish id from content parts + timestamp salt avoided for idempotent tests use hash of content+entities."""
    digest = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:12]
    return f"mem_{digest}"


class MemoryStoreService:
    """add / update / get / list_for_entity / promote / decay."""

    def __init__(self, path: Path | str | None = None, settings: Settings | None = None) -> None:
        self._settings = settings if settings is not None else get_settings()
        self.path = Path(path) if path is not None else hmem_db_path(self._settings)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def add(self, memory: Memory) -> Memory:
        """Insert a memory row."""
        self._conn.execute(
            """
            INSERT INTO memories(
                id, content, type, layer, entity_ids_json, tags_json, embedding_json,
                confidence, source, timestamp, last_accessed, access_count, supersedes,
                derived_from_json, agent_id, run_id, attribute_key, attribute_value, superseded_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            self._to_row(memory),
        )
        self._conn.commit()
        return memory

    def update(self, memory: Memory) -> Memory:
        """Replace a memory row by id (preserves superseded_by)."""
        self._conn.execute(
            """
            UPDATE memories SET
                content = ?, type = ?, layer = ?, entity_ids_json = ?, tags_json = ?,
                embedding_json = ?, confidence = ?, source = ?, timestamp = ?,
                last_accessed = ?, access_count = ?, supersedes = ?, derived_from_json = ?,
                agent_id = ?, run_id = ?, attribute_key = ?, attribute_value = ?
            WHERE id = ?
            """,
            (
                memory.content,
                memory.type,
                memory.layer,
                json.dumps(memory.entity_ids),
                json.dumps(memory.tags),
                json.dumps(memory.embedding),
                memory.confidence,
                memory.source,
                memory.timestamp,
                memory.last_accessed,
                memory.access_count,
                memory.supersedes,
                json.dumps(memory.derived_from),
                memory.agent_id,
                memory.run_id,
                memory.attribute_key,
                memory.attribute_value,
                memory.id,
            ),
        )
        self._conn.commit()
        return memory

    def get(self, memory_id: str) -> Memory | None:
        row = self._conn.execute(
            "SELECT * FROM memories WHERE id = ?", (memory_id,)
        ).fetchone()
        return None if row is None else self._from_row(row)

    def list_all(self) -> list[Memory]:
        rows = self._conn.execute("SELECT * FROM memories ORDER BY timestamp DESC").fetchall()
        return [self._from_row(row) for row in rows]

    def list_active(self) -> list[Memory]:
        """Memories not marked superseded_by another record."""
        rows = self._conn.execute(
            "SELECT * FROM memories WHERE superseded_by = '' ORDER BY timestamp DESC"
        ).fetchall()
        return [self._from_row(row) for row in rows]

    def list_for_entity(self, entity_id: str, *, active_only: bool = True) -> list[Memory]:
        rows = self._conn.execute("SELECT * FROM memories ORDER BY timestamp DESC").fetchall()
        out: list[Memory] = []
        for row in rows:
            if active_only and row["superseded_by"]:
                continue
            mem = self._from_row(row)
            if entity_id in mem.entity_ids:
                out.append(mem)
        return out

    def find_attribute_conflicts(
        self,
        *,
        entity_ids: list[str],
        attribute_key: str,
        attribute_value: str,
    ) -> list[Memory]:
        """Active memories for overlapping entities with same attribute key, different value."""
        if not attribute_key or not attribute_value:
            return []
        key = attribute_key.strip().lower()
        value = attribute_value.strip().casefold()
        entity_set = set(entity_ids)
        conflicts: list[Memory] = []
        for mem in self.list_active():
            if mem.attribute_key.strip().lower() != key:
                continue
            if mem.attribute_value.strip().casefold() == value:
                continue
            if entity_set & set(mem.entity_ids):
                conflicts.append(mem)
        return conflicts

    def mark_superseded(self, older_id: str, newer_id: str) -> Memory | None:
        """Mark older memory superseded by newer; set newer.supersedes = older_id."""
        older = self.get(older_id)
        newer = self.get(newer_id)
        if older is None or newer is None:
            return None
        self._conn.execute(
            "UPDATE memories SET superseded_by = ? WHERE id = ?",
            (newer_id, older_id),
        )
        newer.supersedes = older_id
        self._conn.execute(
            "UPDATE memories SET supersedes = ? WHERE id = ?",
            (older_id, newer_id),
        )
        self._conn.commit()
        return self.get(newer_id)

    def promote(self, memory_id: str, *, reason: str = "") -> Memory | None:
        """Promote short_term/working -> long_term."""
        _ = reason
        mem = self.get(memory_id)
        if mem is None:
            return None
        if mem.layer == "long_term":
            return mem
        mem.layer = "long_term"
        mem.last_accessed = _now()
        return self.update(mem)

    def maybe_promote_by_access(self, memory_id: str) -> Memory | None:
        """Promote when access_count >= 3."""
        mem = self.get(memory_id)
        if mem is None:
            return None
        if mem.access_count >= 3 and mem.layer != "long_term":
            return self.promote(memory_id, reason="access_count")
        return mem

    def bump_access(self, memory_id: str) -> Memory | None:
        mem = self.get(memory_id)
        if mem is None:
            return None
        mem.access_count += 1
        mem.last_accessed = _now()
        self.update(mem)
        return self.maybe_promote_by_access(memory_id)

    def decay(self, *, as_of: datetime | None = None) -> int:
        """Apply confidence *= 0.98 per day for non-long_term memories. Returns count updated."""
        now = as_of or datetime.now(timezone.utc)
        updated = 0
        for mem in self.list_all():
            if mem.layer == "long_term":
                continue
            base = _parse_ts(mem.last_accessed or mem.timestamp)
            days = max(0, (now - base).total_seconds() / 86400.0)
            if days < 1:
                continue
            factor = math.pow(0.98, days)
            new_conf = max(0.0, min(1.0, mem.confidence * factor))
            if abs(new_conf - mem.confidence) < 1e-9:
                continue
            mem.confidence = new_conf
            # Decay applies from the original timestamp; stamp last_accessed so
            # repeated decay calls within the same day do not stack incorrectly.
            mem.last_accessed = now.strftime("%Y-%m-%dT%H:%M:%SZ")
            self.update(mem)
            updated += 1
        return updated

    def remember(self, text: str) -> None:
        """Legacy placeholder — use MemoryStoreService.add."""
        _ = text
        raise NotImplementedError("Use MemoryStoreService.add")

    @staticmethod
    def _to_row(memory: Memory) -> tuple[object, ...]:
        return (
            memory.id,
            memory.content,
            memory.type,
            memory.layer,
            json.dumps(memory.entity_ids),
            json.dumps(memory.tags),
            json.dumps(memory.embedding),
            memory.confidence,
            memory.source,
            memory.timestamp,
            memory.last_accessed,
            memory.access_count,
            memory.supersedes,
            json.dumps(memory.derived_from),
            memory.agent_id,
            memory.run_id,
            memory.attribute_key,
            memory.attribute_value,
            "",
        )

    @staticmethod
    def _from_row(row: sqlite3.Row) -> Memory:
        return Memory(
            id=str(row["id"]),
            content=str(row["content"]),
            type=row["type"],
            layer=row["layer"],
            entity_ids=list(json.loads(row["entity_ids_json"] or "[]")),
            tags=list(json.loads(row["tags_json"] or "[]")),
            embedding=list(json.loads(row["embedding_json"] or "[]")),
            confidence=float(row["confidence"] or 0.7),
            source=row["source"],
            timestamp=str(row["timestamp"] or ""),
            last_accessed=str(row["last_accessed"] or ""),
            access_count=int(row["access_count"] or 0),
            supersedes=str(row["supersedes"] or ""),
            derived_from=list(json.loads(row["derived_from_json"] or "[]")),
            agent_id=str(row["agent_id"] or "hmem"),
            run_id=str(row["run_id"] or ""),
            attribute_key=str(row["attribute_key"] or ""),
            attribute_value=str(row["attribute_value"] or ""),
        )
