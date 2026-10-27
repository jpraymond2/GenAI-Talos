"""Classify an utterance into an Intent."""

from __future__ import annotations

from typing import Any

from recall.agents import offline
from recall.agents.prompts import ROUTER_SYSTEM
from recall.agents.time_context import with_today
from recall.llm.generator import SampleCacheMiss, generate_json
from recall.schemas import Intent, RunState
from recall.tracing import Tracer


async def run(state: RunState, deps: Any) -> RunState:
    """Route the utterance and store Intent on the run state."""
    prompt = with_today(
        f"UTTERANCE: {state.utterance.text}\n"
        f"MODE: {state.utterance.mode}\n"
        f"TS: {state.utterance.ts.isoformat()}",
        settings=getattr(deps, "settings", None),
    )
    generate_json_fn = getattr(deps, "generate_json", None) or generate_json
    try:
        intent = await generate_json_fn(prompt, ROUTER_SYSTEM, Intent, role="router")
        assert isinstance(intent, Intent)
        summary = (
            f"kind={intent.kind} person={intent.person_name!r} "
            f"confidence={intent.confidence:.2f} sources={len(intent.sources_plan)}"
        )
        _log(deps, "router", summary, {"intent": intent.model_dump(mode="json")})
    except SampleCacheMiss:
        intent = offline.route_intent(
            state.utterance.text,
            deps=deps,
            settings=getattr(deps, "settings", None),
        )
    state.intent = intent
    return state


def _log(deps: Any, agent: str, summary: str, payload: dict[str, Any]) -> None:
    tracer = getattr(deps, "tracer", None) or Tracer.current()
    if tracer is not None:
        tracer.event(agent, "complete", {"summary": summary, **payload})
