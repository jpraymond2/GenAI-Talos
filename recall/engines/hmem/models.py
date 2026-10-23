"""Pydantic models for the Meterless H-MEM engine (step 3.2)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

MemoryType = Literal["personal", "factual", "preference", "procedural"]
MemoryLayer = Literal["short_term", "working", "long_term"]
MemorySource = Literal["user_note", "critic", "action", "retrieval"]


class Memory(BaseModel):
    """One hierarchical memory record."""

    id: str
    content: str
    type: MemoryType = "factual"
    layer: MemoryLayer = "short_term"
    entity_ids: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    embedding: list[float] = Field(default_factory=list)
    confidence: float = 0.7
    source: MemorySource = "user_note"
    timestamp: str = ""
    last_accessed: str = ""
    access_count: int = 0
    supersedes: str = ""
    derived_from: list[str] = Field(default_factory=list)
    agent_id: str = "hmem"
    run_id: str = ""
    # Soft attribute key for conflict checks (city, employer); not always set.
    attribute_key: str = ""
    attribute_value: str = ""


class MinedAtomicMemory(BaseModel):
    """One atomic memory from generate_json mining."""

    content: str
    type: MemoryType = "factual"
    entity_names: list[str] = Field(default_factory=list)
    attribute_key: str = ""
    attribute_value: str = ""


class MinedMemoryBatch(BaseModel):
    """Schema passed to generate_json(role='memory_extract')."""

    memories: list[MinedAtomicMemory] = Field(default_factory=list)


class MineEvent(BaseModel):
    """Input event for MemoryMiningService.mine."""

    kind: Literal["user_note", "critic_verdict", "action_outcome"] = "user_note"
    text: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class RetrievedMemory(BaseModel):
    """One scored hit from hybrid retrieval."""

    id: str
    content: str
    score: float
    why: str


class RetrieveResult(BaseModel):
    """Retrieval payload for the orchestrator."""

    memories: list[RetrievedMemory] = Field(default_factory=list)
    strategy: str = "hybrid"
    retrieval_reason: str = ""
