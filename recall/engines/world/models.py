"""Pydantic models for the Meterless World Model (step 3.1)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

EntityType = Literal["person", "org", "place"]
RelationshipType = Literal[
    "works_at",
    "attended",
    "knows",
    "lives_in",
    "social",
    "professional",
    "mixed",
    "family",
    "acquaintance",
]
RelationshipLabel = Literal["social", "professional", "mixed", "family", "acquaintance"]


class AttributeObservation(BaseModel):
    """One observed attribute value with provenance."""

    field: str = ""
    value: str
    observed_at: str
    source_ref: str


class Entity(BaseModel):
    """A person, org, or place in the world store."""

    id: str
    type: EntityType = "person"
    canonical_name: str
    aliases: list[str] = Field(default_factory=list)
    attributes: dict[str, str] = Field(default_factory=dict)
    attribute_history: list[AttributeObservation] = Field(default_factory=list)
    first_seen_at: str = ""
    last_seen_at: str = ""
    mention_count: int = 0
    confidence: float = 0.5
    agent_id: str = "world_ingest"
    run_id: str = ""


class Relationship(BaseModel):
    """A typed edge between two entities."""

    id: str
    from_id: str
    to_id: str
    type: RelationshipType
    valid_from: str = ""
    source_ref: str = ""
    confidence: float = 0.5


class RelationshipSignal(BaseModel):
    """One computed relationship signal with provenance refs."""

    name: str
    value: float | str | int
    source_refs: list[str] = Field(default_factory=list)


class RelationshipAssessment(BaseModel):
    """Classified relationship from the user to another person."""

    entity_id: str
    label: RelationshipLabel = "acquaintance"
    confidence: float = 0.0
    signals: list[RelationshipSignal] = Field(default_factory=list)
    reasoning: str = ""
    scoring_version: int = 1
    assessed_at: str = ""
    status: Literal["auto", "user_override"] = "auto"
    interaction_count_at_assess: int = 0
    score_file: str = ""
    user_entity_id: str = ""
    run_id: str = ""


class PreferenceItem(BaseModel):
    """One food/place preference attributed to a person."""

    kind: Literal[
        "cuisine",
        "restaurant",
        "cafe",
        "bar",
        "hangout",
        "neighbourhood",
        "dislike",
        "dietary_stated",
    ]
    value: str
    sentiment: Literal["likes", "dislikes", "neutral"] = "likes"
    mention_count: int = 1
    last_mentioned: str = ""
    source_refs: list[str] = Field(default_factory=list)
    confidence: float = 0.5


class PreferenceProfile(BaseModel):
    """On-demand food/places preference profile for one person."""

    entity_id: str
    topic: str = "food_places"
    items: list[PreferenceItem] = Field(default_factory=list)
    built_at: str = ""
    scoring_version: int = 1
    interaction_count_at_build: int = 0
    opted_out: bool = False


class Observation(BaseModel):
    """Append-only log of every fact written."""

    entity_id: str
    field: str
    value: str
    source_ref: str = ""
    agent_id: str = "world_ingest"
    run_id: str = ""
    ts: str = ""


class ConflictValue(BaseModel):
    """One competing attribute value inside a Conflict."""

    value: str
    source_ref: str
    observed_at: str = ""


class Conflict(BaseModel):
    """Two or more disagreeing values for the same entity field."""

    id: str
    entity_id: str
    field: str
    values: list[ConflictValue] = Field(default_factory=list)


class ResolveCandidate(BaseModel):
    """Ranked entity match for a name query."""

    entity_id: str
    name: str
    score: float
    reason: str


class PersonContext(BaseModel):
    """Structured person brief for the orchestrator (not a free-form string)."""

    entity: Entity
    aliases: list[str] = Field(default_factory=list)
    last_interaction_per_source: dict[str, dict[str, Any]] = Field(default_factory=dict)
    recent_interactions: list[dict[str, Any]] = Field(default_factory=list)
    open_loops: list[dict[str, Any]] = Field(default_factory=list)
    upcoming_events: list[dict[str, Any]] = Field(default_factory=list)
    past_events: list[dict[str, Any]] = Field(default_factory=list)
    conflicts: list[Conflict] = Field(default_factory=list)
    selection_reason: str = ""
