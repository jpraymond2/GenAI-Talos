"""SQLite persistence for the Meterless World Model."""

from __future__ import annotations

import hashlib
import json
import re
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
    PreferenceItem,
    PreferenceProfile,
    Relationship,
    RelationshipAssessment,
    RelationshipSignal,
)

_SAMPLE_TABLE_FILES = (
    "contacts.csv",
    "handles.csv",
    "interactions.csv",
    "events.csv",
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

CREATE TABLE IF NOT EXISTS identity_assumptions (
    id TEXT PRIMARY KEY,
    handle_kind TEXT NOT NULL,
    handle_value TEXT NOT NULL,
    candidate_entity_id TEXT,
    raw_score REAL NOT NULL DEFAULT 0,
    probability REAL NOT NULL DEFAULT 0,
    rank INTEGER NOT NULL DEFAULT 0,
    decision TEXT NOT NULL,
    status TEXT NOT NULL,
    scoring_version INTEGER NOT NULL DEFAULT 1,
    run_id TEXT NOT NULL DEFAULT '',
    decided_at TEXT NOT NULL DEFAULT '',
    score_file TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS employment_assumptions (
    id TEXT PRIMARY KEY,
    entity_id TEXT NOT NULL,
    company TEXT NOT NULL,
    probability REAL NOT NULL DEFAULT 0,
    supporting_refs TEXT NOT NULL DEFAULT '[]',
    contradicting_refs TEXT NOT NULL DEFAULT '[]',
    scoring_version INTEGER NOT NULL DEFAULT 1,
    run_id TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    valid_from TEXT NOT NULL DEFAULT '',
    valid_to TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS identity_questions (
    id TEXT PRIMARY KEY,
    handle_kind TEXT NOT NULL,
    handle_value TEXT NOT NULL,
    candidate_a TEXT,
    candidate_b TEXT,
    question TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    evidence_hash TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT '',
    resolved_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS handle_links (
    id TEXT PRIMARY KEY,
    entity_id TEXT NOT NULL,
    handle_kind TEXT NOT NULL,
    handle_value TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 1.0,
    status TEXT NOT NULL DEFAULT 'active',
    source_ref TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT '',
    UNIQUE(entity_id, handle_kind, handle_value)
);

CREATE TABLE IF NOT EXISTS relationship_assessments (
    id TEXT PRIMARY KEY,
    entity_id TEXT NOT NULL,
    user_entity_id TEXT NOT NULL DEFAULT '',
    label TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0,
    signals_json TEXT NOT NULL DEFAULT '[]',
    reasoning TEXT NOT NULL DEFAULT '',
    scoring_version INTEGER NOT NULL DEFAULT 1,
    assessed_at TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'auto',
    interaction_count_at_assess INTEGER NOT NULL DEFAULT 0,
    score_file TEXT NOT NULL DEFAULT '',
    run_id TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS preference_profiles (
    entity_id TEXT NOT NULL,
    topic TEXT NOT NULL DEFAULT 'food_places',
    items_json TEXT NOT NULL DEFAULT '[]',
    built_at TEXT NOT NULL DEFAULT '',
    scoring_version INTEGER NOT NULL DEFAULT 1,
    interaction_count_at_build INTEGER NOT NULL DEFAULT 0,
    opted_out INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (entity_id, topic)
);

CREATE INDEX IF NOT EXISTS idx_entities_name ON entities(canonical_name);
CREATE INDEX IF NOT EXISTS idx_conflicts_entity ON conflicts(entity_id);
CREATE INDEX IF NOT EXISTS idx_observations_entity ON observations(entity_id);
CREATE INDEX IF NOT EXISTS idx_identity_handle ON identity_assumptions(handle_value, status);
CREATE INDEX IF NOT EXISTS idx_employment_entity ON employment_assumptions(entity_id, status);
CREATE INDEX IF NOT EXISTS idx_handle_links_value ON handle_links(handle_value, status);
CREATE INDEX IF NOT EXISTS idx_rel_assess_entity ON relationship_assessments(entity_id, assessed_at);
"""


def _safe_dataset_name(settings: Settings) -> str:
    """Filesystem-safe dataset id for per-dataset SQLite paths."""
    raw = (settings.dataset_name or "").strip() or "sample"
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", raw).strip("_")
    return safe or "sample"


def source_tables_fingerprint(sample_dir: Path | str) -> str:
    """Sha256 of the four sample CSVs (contacts/handles/interactions/events)."""
    root = Path(sample_dir)
    digest = hashlib.sha256()
    for name in _SAMPLE_TABLE_FILES:
        path = root / name
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        if path.is_file():
            digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def world_db_path(settings: Settings | None = None) -> Path:
    """Return ``data/world_<dataset>.db`` in SAMPLE_MODE, else ``data/world.db``."""
    active = settings if settings is not None else get_settings()
    if not active.sample_mode:
        return Path("data/world.db")
    return Path(f"data/world_{_safe_dataset_name(active)}.db")


class WorldStore:
    """SQLite-backed world store of entities, relationships, observations, conflicts."""

    def __init__(self, path: Path | str | None = None, settings: Settings | None = None) -> None:
        """Open (or create) the world database at ``path``."""
        self.path = Path(path) if path is not None else world_db_path(settings)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False: ingest may run identity scoring on a worker
        # thread when already inside an asyncio loop (bench / async callers).
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._migrate_schema()
        self._conn.commit()

    def _migrate_schema(self) -> None:
        """Additive migrations for older world DBs."""
        cols = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(employment_assumptions)").fetchall()
        }
        if cols and "valid_from" not in cols:
            self._conn.execute(
                "ALTER TABLE employment_assumptions ADD COLUMN valid_from TEXT NOT NULL DEFAULT ''"
            )
        if cols and "valid_to" not in cols:
            self._conn.execute(
                "ALTER TABLE employment_assumptions ADD COLUMN valid_to TEXT NOT NULL DEFAULT ''"
            )

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

    def list_relationships(
        self,
        *,
        from_id: str | None = None,
        to_id: str | None = None,
        type: str | None = None,
    ) -> list[Relationship]:
        """List relationship edges with optional filters."""
        sql = "SELECT * FROM relationships WHERE 1=1"
        params: list[Any] = []
        if from_id:
            sql += " AND from_id = ?"
            params.append(from_id)
        if to_id:
            sql += " AND to_id = ?"
            params.append(to_id)
        if type:
            sql += " AND type = ?"
            params.append(type)
        rows = self._conn.execute(sql, params).fetchall()
        return [
            Relationship(
                id=str(r["id"]),
                from_id=str(r["from_id"]),
                to_id=str(r["to_id"]),
                type=r["type"],  # type: ignore[arg-type]
                valid_from=str(r["valid_from"] or ""),
                source_ref=str(r["source_ref"] or ""),
                confidence=float(r["confidence"] or 0.5),
            )
            for r in rows
        ]

    def insert_relationship_assessment(self, assessment: RelationshipAssessment) -> None:
        """Append one relationship assessment (history preserved)."""
        assess_id = (
            f"rel_{assessment.entity_id}_{assessment.assessed_at}_{assessment.run_id}"[:80]
        )
        self._conn.execute(
            """
            INSERT INTO relationship_assessments(
                id, entity_id, user_entity_id, label, confidence, signals_json,
                reasoning, scoring_version, assessed_at, status,
                interaction_count_at_assess, score_file, run_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                assess_id,
                assessment.entity_id,
                assessment.user_entity_id,
                assessment.label,
                float(assessment.confidence),
                json.dumps(
                    [s.model_dump() for s in assessment.signals], ensure_ascii=False
                ),
                assessment.reasoning,
                int(assessment.scoring_version),
                assessment.assessed_at,
                assessment.status,
                int(assessment.interaction_count_at_assess),
                assessment.score_file,
                assessment.run_id,
            ),
        )
        self._conn.commit()

    def latest_relationship_assessment(
        self, entity_id: str
    ) -> RelationshipAssessment | None:
        """Latest assessment for a person (any status)."""
        row = self._conn.execute(
            """
            SELECT * FROM relationship_assessments
            WHERE entity_id = ?
            ORDER BY assessed_at DESC
            LIMIT 1
            """,
            (entity_id,),
        ).fetchone()
        return None if row is None else self._row_to_relationship_assessment(row)

    def list_relationship_assessments(
        self, *, latest_only: bool = True
    ) -> list[RelationshipAssessment]:
        """List assessments; when latest_only, one row per entity_id."""
        rows = self._conn.execute(
            """
            SELECT * FROM relationship_assessments
            ORDER BY assessed_at DESC
            """
        ).fetchall()
        items = [self._row_to_relationship_assessment(r) for r in rows]
        if not latest_only:
            return items
        seen: set[str] = set()
        out: list[RelationshipAssessment] = []
        for item in items:
            if item.entity_id in seen:
                continue
            seen.add(item.entity_id)
            out.append(item)
        return out

    def upsert_preference_profile(self, profile: PreferenceProfile) -> None:
        """Insert or replace a cached preference profile."""
        self._conn.execute(
            """
            INSERT INTO preference_profiles(
                entity_id, topic, items_json, built_at, scoring_version,
                interaction_count_at_build, opted_out
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(entity_id, topic) DO UPDATE SET
                items_json = excluded.items_json,
                built_at = excluded.built_at,
                scoring_version = excluded.scoring_version,
                interaction_count_at_build = excluded.interaction_count_at_build,
                opted_out = excluded.opted_out
            """,
            (
                profile.entity_id,
                profile.topic,
                json.dumps(
                    [i.model_dump() for i in profile.items], ensure_ascii=False
                ),
                profile.built_at,
                int(profile.scoring_version),
                int(profile.interaction_count_at_build),
                1 if profile.opted_out else 0,
            ),
        )
        self._conn.commit()

    def get_preference_profile(
        self, entity_id: str, topic: str = "food_places"
    ) -> PreferenceProfile | None:
        """Load cached preference profile if present."""
        row = self._conn.execute(
            """
            SELECT * FROM preference_profiles
            WHERE entity_id = ? AND topic = ?
            """,
            (entity_id, topic),
        ).fetchone()
        if row is None:
            return None
        items_raw = json.loads(row["items_json"] or "[]")
        return PreferenceProfile(
            entity_id=str(row["entity_id"]),
            topic=str(row["topic"]),
            items=[PreferenceItem.model_validate(i) for i in items_raw],
            built_at=str(row["built_at"] or ""),
            scoring_version=int(row["scoring_version"] or 1),
            interaction_count_at_build=int(row["interaction_count_at_build"] or 0),
            opted_out=bool(row["opted_out"]),
        )

    def delete_preference_profile(
        self, entity_id: str, topic: str = "food_places"
    ) -> None:
        """Remove a preference profile (opt-out leaves a tombstone via upsert)."""
        self._conn.execute(
            "DELETE FROM preference_profiles WHERE entity_id = ? AND topic = ?",
            (entity_id, topic),
        )
        self._conn.commit()

    @staticmethod
    def _row_to_relationship_assessment(row: sqlite3.Row) -> RelationshipAssessment:
        signals_raw = json.loads(row["signals_json"] or "[]")
        return RelationshipAssessment(
            entity_id=str(row["entity_id"]),
            user_entity_id=str(row["user_entity_id"] or ""),
            label=row["label"],  # type: ignore[arg-type]
            confidence=float(row["confidence"] or 0),
            signals=[RelationshipSignal.model_validate(s) for s in signals_raw],
            reasoning=str(row["reasoning"] or ""),
            scoring_version=int(row["scoring_version"] or 1),
            assessed_at=str(row["assessed_at"] or ""),
            status=row["status"],  # type: ignore[arg-type]
            interaction_count_at_assess=int(row["interaction_count_at_assess"] or 0),
            score_file=str(row["score_file"] or ""),
            run_id=str(row["run_id"] or ""),
        )

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

    def insert_identity_assumption(self, row: dict[str, Any]) -> None:
        """Append one identity assumption row (never updates in place)."""
        self._conn.execute(
            """
            INSERT INTO identity_assumptions(
                id, handle_kind, handle_value, candidate_entity_id, raw_score,
                probability, rank, decision, status, scoring_version, run_id,
                decided_at, score_file
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["id"],
                row["handle_kind"],
                row["handle_value"],
                row.get("candidate_entity_id"),
                float(row.get("raw_score", 0)),
                float(row.get("probability", 0)),
                int(row.get("rank", 0)),
                row["decision"],
                row["status"],
                int(row.get("scoring_version", 1)),
                row.get("run_id", ""),
                row.get("decided_at", ""),
                row.get("score_file", ""),
            ),
        )
        self._conn.commit()

    def supersede_active_auto_assumptions(self, handle_value: str) -> None:
        """Supersede active rows; leave confirmed_by_user / rejected_by_user alone."""
        self._conn.execute(
            """
            UPDATE identity_assumptions
            SET status = 'superseded'
            WHERE lower(handle_value) = lower(?)
              AND status = 'active'
            """,
            (handle_value,),
        )
        self._conn.commit()

    def list_identity_assumptions(
        self,
        *,
        status: str | None = None,
        handle_value: str | None = None,
        active_only: bool = False,
    ) -> list[dict[str, Any]]:
        """List identity assumptions with optional filters."""
        sql = "SELECT * FROM identity_assumptions WHERE 1=1"
        params: list[Any] = []
        if status:
            # decision filter used by CLI --status needs_user|tentative
            sql += " AND decision = ?"
            params.append(status)
        if handle_value:
            sql += " AND lower(handle_value) = lower(?)"
            params.append(handle_value)
        if active_only:
            sql += " AND status = 'active'"
        sql += " ORDER BY decided_at DESC, rank ASC"
        rows = self._conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def latest_identity_for_handle(self, handle_value: str) -> dict[str, Any] | None:
        """Latest non-superseded assumption for a handle (prefer active/user)."""
        row = self._conn.execute(
            """
            SELECT * FROM identity_assumptions
            WHERE lower(handle_value) = lower(?)
              AND status IN ('active', 'confirmed_by_user', 'rejected_by_user')
            ORDER BY
              CASE status
                WHEN 'confirmed_by_user' THEN 0
                WHEN 'rejected_by_user' THEN 1
                ELSE 2
              END,
              decided_at DESC
            LIMIT 1
            """,
            (handle_value,),
        ).fetchone()
        return dict(row) if row else None

    def user_locked_handle(self, handle_value: str) -> dict[str, Any] | None:
        """Return confirmed/rejected user decision if present."""
        row = self._conn.execute(
            """
            SELECT * FROM identity_assumptions
            WHERE lower(handle_value) = lower(?)
              AND status IN ('confirmed_by_user', 'rejected_by_user')
            ORDER BY decided_at DESC
            LIMIT 1
            """,
            (handle_value,),
        ).fetchone()
        return dict(row) if row else None

    def upsert_employment_assumption(self, row: dict[str, Any]) -> None:
        """Insert or update active employment assumption for entity+company."""
        existing = self._conn.execute(
            """
            SELECT id FROM employment_assumptions
            WHERE entity_id = ? AND lower(company) = lower(?) AND status = 'active'
            """,
            (row["entity_id"], row["company"]),
        ).fetchone()
        if existing:
            self._conn.execute(
                """
                UPDATE employment_assumptions
                SET probability = ?, supporting_refs = ?, contradicting_refs = ?,
                    scoring_version = ?, run_id = ?, updated_at = ?,
                    valid_from = ?, valid_to = ?
                WHERE id = ?
                """,
                (
                    float(row.get("probability", 0)),
                    json.dumps(row.get("supporting_refs") or [], ensure_ascii=False),
                    json.dumps(row.get("contradicting_refs") or [], ensure_ascii=False),
                    int(row.get("scoring_version", 1)),
                    row.get("run_id", ""),
                    row.get("updated_at", ""),
                    row.get("valid_from", ""),
                    row.get("valid_to", ""),
                    str(existing["id"]),
                ),
            )
        else:
            self._conn.execute(
                """
                INSERT INTO employment_assumptions(
                    id, entity_id, company, probability, supporting_refs,
                    contradicting_refs, scoring_version, run_id, updated_at, status,
                    valid_from, valid_to
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["id"],
                    row["entity_id"],
                    row["company"],
                    float(row.get("probability", 0)),
                    json.dumps(row.get("supporting_refs") or [], ensure_ascii=False),
                    json.dumps(row.get("contradicting_refs") or [], ensure_ascii=False),
                    int(row.get("scoring_version", 1)),
                    row.get("run_id", ""),
                    row.get("updated_at", ""),
                    row.get("status", "active"),
                    row.get("valid_from", ""),
                    row.get("valid_to", ""),
                ),
            )
        self._conn.commit()

    def list_employment_assumptions(
        self, entity_id: str | None = None
    ) -> list[dict[str, Any]]:
        """List employment assumptions."""
        if entity_id:
            rows = self._conn.execute(
                "SELECT * FROM employment_assumptions WHERE entity_id = ? ORDER BY probability DESC",
                (entity_id,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM employment_assumptions ORDER BY probability DESC"
            ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["supporting_refs"] = json.loads(item.get("supporting_refs") or "[]")
            item["contradicting_refs"] = json.loads(item.get("contradicting_refs") or "[]")
            out.append(item)
        return out

    def upsert_handle_link(self, row: dict[str, Any]) -> None:
        """Create/replace an active handle alias link on an entity."""
        self._conn.execute(
            """
            INSERT INTO handle_links(
                id, entity_id, handle_kind, handle_value, confidence, status, source_ref, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                confidence = excluded.confidence,
                status = excluded.status,
                source_ref = excluded.source_ref
            """,
            (
                row["id"],
                row["entity_id"],
                row["handle_kind"],
                row["handle_value"],
                float(row.get("confidence", 1.0)),
                row.get("status", "active"),
                row.get("source_ref", ""),
                row.get("created_at", ""),
            ),
        )
        self._conn.commit()

    def list_handle_links(
        self, *, entity_id: str | None = None, handle_value: str | None = None
    ) -> list[dict[str, Any]]:
        """List handle links."""
        sql = "SELECT * FROM handle_links WHERE status = 'active'"
        params: list[Any] = []
        if entity_id:
            sql += " AND entity_id = ?"
            params.append(entity_id)
        if handle_value:
            sql += " AND lower(handle_value) = lower(?)"
            params.append(handle_value)
        rows = self._conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def add_entity_alias(self, entity_id: str, alias: str) -> None:
        """Append an alias string onto an entity if missing."""
        entity = self.get_entity(entity_id)
        if entity is None:
            return
        if alias in entity.aliases:
            return
        entity.aliases = [*entity.aliases, alias]
        self.upsert_entity(entity)

    def rejected_entity_ids_for_handle(self, handle_value: str) -> set[str]:
        """Entity ids the user rejected for this handle."""
        rows = self._conn.execute(
            """
            SELECT candidate_entity_id FROM identity_assumptions
            WHERE lower(handle_value) = lower(?)
              AND status = 'rejected_by_user'
              AND candidate_entity_id IS NOT NULL
            """,
            (handle_value,),
        ).fetchall()
        return {str(r["candidate_entity_id"]) for r in rows if r["candidate_entity_id"]}

    def enqueue_identity_question(self, row: dict[str, Any]) -> None:
        """Queue a needs_user question unless same handle+evidence already asked."""
        existing = self._conn.execute(
            """
            SELECT id FROM identity_questions
            WHERE lower(handle_value) = lower(?)
              AND evidence_hash = ?
              AND status = 'open'
            """,
            (row["handle_value"], row.get("evidence_hash", "")),
        ).fetchone()
        if existing:
            return
        self._conn.execute(
            """
            INSERT INTO identity_questions(
                id, handle_kind, handle_value, candidate_a, candidate_b,
                question, status, evidence_hash, created_at, resolved_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["id"],
                row["handle_kind"],
                row["handle_value"],
                row.get("candidate_a"),
                row.get("candidate_b"),
                row["question"],
                row.get("status", "open"),
                row.get("evidence_hash", ""),
                row.get("created_at", ""),
                row.get("resolved_at", ""),
            ),
        )
        self._conn.commit()

    def list_identity_questions(self, *, status: str = "open") -> list[dict[str, Any]]:
        """List batched clarify questions."""
        rows = self._conn.execute(
            """
            SELECT * FROM identity_questions
            WHERE status = ?
            ORDER BY created_at ASC
            """,
            (status,),
        ).fetchall()
        return [dict(r) for r in rows]
