"""SQLite persistence for the Meterless World Model."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from recall.config import Settings, get_settings
from recall.engines.world.models import (
    AttributeObservation,
    Conflict,
    ConflictValue,
    Entity,
    Observation,
    Relationship,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS entities (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    canonical_name TEXT NOT NULL,
    aliases_json TEXT NOT NULL DEFAULT '[]',
    attributes_json TEXT NOT NULL DEFAULT '{}',
    attribute_history_json TEXT NOT NULL DEFAULT '[]',
    first_seen_at TEXT NOT NULL DEFAULT '',
    last_seen_at TEXT NOT NULL DEFAULT '',
    mention_count INTEGER NOT NULL DEFAULT 0,
    confidence REAL NOT NULL DEFAULT 0.5,
    agent_id TEXT NOT NULL DEFAULT 'world_ingest',
    run_id TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS relationships (
    id TEXT PRIMARY KEY,
    from_id TEXT NOT NULL,
    to_id TEXT NOT NULL,
    type TEXT NOT NULL,
    valid_from TEXT NOT NULL DEFAULT '',
    source_ref TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL DEFAULT 0.5
);

CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id TEXT NOT NULL,
    field TEXT NOT NULL,
    value TEXT NOT NULL,
    source_ref TEXT NOT NULL DEFAULT '',
    agent_id TEXT NOT NULL DEFAULT 'world_ingest',
    run_id TEXT NOT NULL DEFAULT '',
    ts TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS conflicts (
    id TEXT PRIMARY KEY,
    entity_id TEXT NOT NULL,
    field TEXT NOT NULL,
    values_json TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS ingest_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_entities_name ON entities(canonical_name);
CREATE INDEX IF NOT EXISTS idx_conflicts_entity ON conflicts(entity_id);
CREATE INDEX IF NOT EXISTS idx_observations_entity ON observations(entity_id);
"""


def world_db_path(settings: Settings | None = None) -> Path:
    """Return data/world_sample.db in SAMPLE_MODE, else data/world.db."""
    active = settings if settings is not None else get_settings()
    return Path("data/world_sample.db" if active.sample_mode else "data/world.db")


class WorldStore:
    """SQLite-backed world store of entities, relationships, observations, conflicts."""

    def __init__(self, path: Path | str | None = None, settings: Settings | None = None) -> None:
        """Open (or create) the world database at ``path``."""
        self.path = Path(path) if path is not None else world_db_path(settings)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        """Close the underlying connection."""
        self._conn.close()

    def __enter__(self) -> WorldStore:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def get_meta(self, key: str) -> str | None:
        """Read an ingest checkpoint / meta value."""
        row = self._conn.execute(
            "SELECT value FROM ingest_meta WHERE key = ?", (key,)
        ).fetchone()
        return None if row is None else str(row["value"])

    def set_meta(self, key: str, value: str) -> None:
        """Write an ingest checkpoint / meta value."""
        self._conn.execute(
            "INSERT INTO ingest_meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self._conn.commit()

    def upsert_entity(self, entity: Entity) -> None:
        """Insert or replace one entity by stable id (idempotent)."""
        self._conn.execute(
            """
            INSERT INTO entities(
                id, type, canonical_name, aliases_json, attributes_json,
                attribute_history_json, first_seen_at, last_seen_at,
                mention_count, confidence, agent_id, run_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                type = excluded.type,
                canonical_name = excluded.canonical_name,
                aliases_json = excluded.aliases_json,
                attributes_json = excluded.attributes_json,
                attribute_history_json = excluded.attribute_history_json,
                first_seen_at = excluded.first_seen_at,
                last_seen_at = excluded.last_seen_at,
                mention_count = excluded.mention_count,
                confidence = excluded.confidence,
                agent_id = excluded.agent_id,
                run_id = excluded.run_id
            """,
            (
                entity.id,
                entity.type,
                entity.canonical_name,
                json.dumps(entity.aliases, ensure_ascii=False),
                json.dumps(entity.attributes, ensure_ascii=False),
                json.dumps(
                    [h.model_dump() for h in entity.attribute_history],
                    ensure_ascii=False,
                ),
                entity.first_seen_at,
                entity.last_seen_at,
                entity.mention_count,
                entity.confidence,
                entity.agent_id,
                entity.run_id,
            ),
        )
        self._conn.commit()

    def get_entity(self, entity_id: str) -> Entity | None:
        """Load one entity by id."""
        row = self._conn.execute(
            "SELECT * FROM entities WHERE id = ?", (entity_id,)
        ).fetchone()
        return None if row is None else self._row_to_entity(row)

    def list_entities(self, *, type: str | None = "person") -> list[Entity]:
        """List entities, optionally filtered by type."""
        if type is None:
            rows = self._conn.execute("SELECT * FROM entities ORDER BY canonical_name").fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM entities WHERE type = ? ORDER BY canonical_name",
                (type,),
            ).fetchall()
        return [self._row_to_entity(row) for row in rows]

    def upsert_relationship(self, rel: Relationship) -> None:
        """Insert or replace a relationship by id."""
        self._conn.execute(
            """
            INSERT INTO relationships(id, from_id, to_id, type, valid_from, source_ref, confidence)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                from_id = excluded.from_id,
                to_id = excluded.to_id,
                type = excluded.type,
                valid_from = excluded.valid_from,
                source_ref = excluded.source_ref,
                confidence = excluded.confidence
            """,
            (
                rel.id,
                rel.from_id,
                rel.to_id,
                rel.type,
                rel.valid_from,
                rel.source_ref,
                rel.confidence,
            ),
        )
        self._conn.commit()

    def append_observation(self, observation: Observation) -> None:
        """Append one observation (never updated in place)."""
        self._conn.execute(
            """
            INSERT INTO observations(entity_id, field, value, source_ref, agent_id, run_id, ts)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                observation.entity_id,
                observation.field,
                observation.value,
                observation.source_ref,
                observation.agent_id,
                observation.run_id,
                observation.ts,
            ),
        )
        self._conn.commit()

    def upsert_conflict(self, conflict: Conflict) -> None:
        """Insert or replace a conflict record."""
        self._conn.execute(
            """
            INSERT INTO conflicts(id, entity_id, field, values_json)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                entity_id = excluded.entity_id,
                field = excluded.field,
                values_json = excluded.values_json
            """,
            (
                conflict.id,
                conflict.entity_id,
                conflict.field,
                json.dumps([v.model_dump() for v in conflict.values], ensure_ascii=False),
            ),
        )
        self._conn.commit()

    def list_conflicts(self, entity_id: str | None = None) -> list[Conflict]:
        """List conflicts, optionally for one entity."""
        if entity_id is None:
            rows = self._conn.execute("SELECT * FROM conflicts").fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM conflicts WHERE entity_id = ?", (entity_id,)
            ).fetchall()
        return [self._row_to_conflict(row) for row in rows]

    def clear_conflicts_for_entity(self, entity_id: str) -> None:
        """Drop existing conflicts for an entity before recomputing."""
        self._conn.execute("DELETE FROM conflicts WHERE entity_id = ?", (entity_id,))
        self._conn.commit()

    def count_entities(self) -> int:
        """Return entity count (for traces)."""
        row = self._conn.execute("SELECT COUNT(*) AS n FROM entities").fetchone()
        return int(row["n"]) if row else 0

    @staticmethod
    def _row_to_entity(row: sqlite3.Row) -> Entity:
        history_raw = json.loads(row["attribute_history_json"] or "[]")
        history = [AttributeObservation.model_validate(item) for item in history_raw]
        return Entity(
            id=str(row["id"]),
            type=row["type"],
            canonical_name=str(row["canonical_name"]),
            aliases=list(json.loads(row["aliases_json"] or "[]")),
            attributes=dict(json.loads(row["attributes_json"] or "{}")),
            attribute_history=history,
            first_seen_at=str(row["first_seen_at"] or ""),
            last_seen_at=str(row["last_seen_at"] or ""),
            mention_count=int(row["mention_count"] or 0),
            confidence=float(row["confidence"] or 0.5),
            agent_id=str(row["agent_id"] or "world_ingest"),
            run_id=str(row["run_id"] or ""),
        )

    @staticmethod
    def _row_to_conflict(row: sqlite3.Row) -> Conflict:
        values_raw = json.loads(row["values_json"] or "[]")
        return Conflict(
            id=str(row["id"]),
            entity_id=str(row["entity_id"]),
            field=str(row["field"]),
            values=[ConflictValue.model_validate(item) for item in values_raw],
        )

    def add(self, text: str) -> None:
        """Legacy placeholder API — unused; prefer upsert_entity."""
        _ = text
        raise NotImplementedError("Use upsert_entity / IngestPipelineService instead")
