"""Agent input and output models (Pydantic v2)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class Utterance(BaseModel):
    """One spoken or typed user turn."""

    text: str
    ts: datetime
    mode: Literal["voice", "text"] = "text"


class Intent(BaseModel):
    """Parsed user intent for a meeting-prep turn."""

    kind: Literal["brief", "note", "action", "question", "unknown"]
    person_name: str | None = None
    when: str | None = None
    action_request: str | None = None
    sources_plan: list[str]
    confidence: float


class Entity(BaseModel):
    """Legacy placeholder used by engine stubs; prefer Candidate in new code."""

    name: str = ""
    kind: str = ""


class Candidate(BaseModel):
    """A resolved person (or entity) candidate with a match score."""

    entity_id: str
    name: str
    score: float
    reason: str


class Evidence(BaseModel):
    """A sourced snippet used to build or critique a brief."""

    id: str
    source: Literal["gmail", "imessage", "whatsapp", "calendar", "web", "memory"]
    source_ref: str
    ts: datetime
    text: str
    url: str | None = None


class TalkingPoint(BaseModel):
    """One thing worth saying in the meeting, backed by evidence ids."""

    text: str
    evidence_ids: list[str]
    kind: Literal["catch_up", "open_loop", "news", "logistics", "memory"]
    priority: int


class Brief(BaseModel):
    """Meeting-prep brief for one person."""

    person: str
    headline: str
    points: list[TalkingPoint]
    caveats: list[str] = Field(default_factory=list)


class PointVerdict(BaseModel):
    """Critic decision for a single talking point."""

    index: int
    decision: Literal["keep", "fix", "drop", "conflict"]
    reason: str
    fix_hint: str | None = None


class CriticVerdict(BaseModel):
    """Whether the brief is good enough, with per-point notes."""

    overall: Literal["pass", "revise"]
    verdicts: list[PointVerdict]
    missing: list[str] = Field(default_factory=list)
    refetch: list[str] = Field(default_factory=list)


class ActionStep(BaseModel):
    """One side-effect tool call proposed to the user."""

    tool: str
    args: dict[str, Any]
    summary: str


class ActionPlan(BaseModel):
    """Side effects to offer after the brief, with a spoken summary."""

    steps: list[ActionStep]
    spoken_summary: str
    requires_confirmation: bool = True


class RunState(BaseModel):
    """Mutable state for a single ask / meeting-prep run."""

    run_id: str
    utterance: Utterance
    intent: Intent | None = None
    candidates: list[Candidate] = Field(default_factory=list)
    entity_id: str | None = None
    entity_name: str | None = None
    context: dict[str, Any] = Field(default_factory=dict)
    memories: list[dict[str, Any]] = Field(default_factory=list)
    evidence: dict[str, Evidence] = Field(default_factory=dict)
    drafts: list[Brief] = Field(default_factory=list)
    verdicts: list[CriticVerdict] = Field(default_factory=list)
    final_brief: Brief | None = None
    action_plan: ActionPlan | None = None
    status: Literal[
        "running",
        "done",
        "needs_clarification",
        "awaiting_confirmation",
        "failed",
    ] = "running"
    round: int = 0
    question: str | None = None
