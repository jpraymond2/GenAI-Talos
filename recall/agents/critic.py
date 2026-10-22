"""Judge a draft brief against evidence (fast Nemotron via ROLE_MODELS)."""

from __future__ import annotations

import json
from typing import Any

from recall.agents.prompts import CRITIC_SYSTEM
from recall.agents.time_context import with_today
from recall.agents.writer import format_evidence_list
from recall.llm.generator import generate_json
from recall.schemas import CriticVerdict, RunState
from recall.tracing import Tracer


async def run(state: RunState, deps: Any) -> RunState:
    """Critique the latest draft brief and append CriticVerdict to state."""
    if not state.drafts:
        raise ValueError("critic requires at least one draft brief on state.drafts")

    draft = state.drafts[-1]
    prompt = with_today(
        "\n".join(
            [
                "Judge staleness relative to TODAY. Prefer newer evidence when sources disagree.",
                "DRAFT_BRIEF:",
                json.dumps(draft.model_dump(mode="json"), indent=2),
                "EVIDENCE:",
                format_evidence_list(state.evidence),
            ]
        )
    )
    generate_json_fn = getattr(deps, "generate_json", None) or generate_json
    verdict = await generate_json_fn(prompt, CRITIC_SYSTEM, CriticVerdict, role="critic")
    assert isinstance(verdict, CriticVerdict)
    state.verdicts.append(verdict)
    if verdict.overall == "pass":
        state.final_brief = draft

    keep = sum(1 for item in verdict.verdicts if item.decision == "keep")
    summary = (
        f"overall={verdict.overall} keep={keep}/{len(verdict.verdicts)} "
        f"missing={len(verdict.missing)} refetch={len(verdict.refetch)}"
    )
    _log(deps, "critic", summary, {"verdict": verdict.model_dump(mode="json")})
    return state


def _log(deps: Any, agent: str, summary: str, payload: dict[str, Any]) -> None:
    tracer = getattr(deps, "tracer", None) or Tracer.current()
    if tracer is not None:
        tracer.event(agent, "complete", {"summary": summary, **payload})
