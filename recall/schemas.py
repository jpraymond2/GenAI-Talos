"""Agent input and output models.

These are stubs. TODO: replace the placeholder fields once each agent contract is fixed.
"""

from pydantic import BaseModel, Field


class Utterance(BaseModel):
    """One spoken or typed user turn. TODO: speaker, timestamps, and audio id."""

    text: str = ""


class Intent(BaseModel):
    """What the user wants. TODO: slots and confidence."""

    name: str = ""


class Entity(BaseModel):
    """A person, place, or thing mentioned in the turn. TODO: ids and aliases."""

    name: str = ""
    kind: str = ""


class Evidence(BaseModel):
    """A sourced snippet used in the brief. TODO: citation and timestamp."""

    source: str = ""
    text: str = ""


class TalkingPoint(BaseModel):
    """One thing worth saying in the meeting. TODO: priority and evidence ids."""

    text: str = ""


class Brief(BaseModel):
    """The meeting-prep brief. TODO: sections and open questions."""

    summary: str = ""
    talking_points: list[TalkingPoint] = Field(default_factory=list)


class CriticVerdict(BaseModel):
    """Whether the brief is good enough to show. TODO: rubric scores."""

    accepted: bool = False
    notes: str = ""


class ActionPlan(BaseModel):
    """Side effects to offer the user. TODO: tool calls."""

    steps: list[str] = Field(default_factory=list)


class RunState(BaseModel):
    """State for a single ask. TODO: fill this in as agents are wired."""

    run_id: str = ""
    utterance: Utterance | None = None
    intent: Intent | None = None
    entities: list[Entity] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    brief: Brief | None = None
    verdict: CriticVerdict | None = None
    actions: ActionPlan | None = None
