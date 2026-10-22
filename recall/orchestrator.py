"""Run one meeting-prep pass end-to-end.

Order: router -> resolver -> retriever -> writer/critic loop (-> actions later).
Each step writes Tracer events. Critic rounds stop at 2; LLM calls stop at 8.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from recall.agents import critic, router, writer
from recall.config import get_settings
from recall.fixtures import load_fixture_evidence
from recall.llm import generator as llm
from recall.schemas import Brief, Candidate, CriticVerdict, RunState, TalkingPoint, Utterance
from recall.tracing import Tracer

logger = logging.getLogger(__name__)

MAX_CRITIC_ROUNDS = 2
MAX_LLM_CALLS = 8

GenerateFn = Callable[..., Awaitable[str]]
GenerateJsonFn = Callable[..., Awaitable[BaseModel]]


class LLMCallLimitExceeded(RuntimeError):
    """Raised when a run exceeds MAX_LLM_CALLS."""


@dataclass
class Deps:
    """Injected dependencies for agents and the orchestrator.

    query_layer / world / hmem are reserved for TODO(step3).
    """

    tracer: Tracer
    generate: GenerateFn = field(default=llm.generate)
    generate_json: GenerateJsonFn = field(default=llm.generate_json)
    query_layer: Any = None  # TODO(step3): wire recall.data.query_layer
    world: Any = None  # TODO(step3): wire engines.world
    hmem: Any = None  # TODO(step3): wire engines.hmem
    max_llm_calls: int = MAX_LLM_CALLS
    llm_calls: int = 0
    _raw_generate: GenerateFn = field(init=False, repr=False)
    _raw_generate_json: GenerateJsonFn = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._raw_generate = self.generate
        self._raw_generate_json = self.generate_json
        self.generate = self._tracked_generate  # type: ignore[method-assign]
        self.generate_json = self._tracked_generate_json  # type: ignore[method-assign]

    async def _tracked_generate(self, *args: Any, **kwargs: Any) -> str:
        self._count_llm_call()
        return await self._raw_generate(*args, **kwargs)

    async def _tracked_generate_json(self, *args: Any, **kwargs: Any) -> BaseModel:
        self._count_llm_call()
        return await self._raw_generate_json(*args, **kwargs)

    def _count_llm_call(self) -> None:
        self.llm_calls += 1
        if self.llm_calls > self.max_llm_calls:
            self.tracer.event(
                "orchestrator",
                "error",
                {"summary": f"LLM call limit exceeded ({self.max_llm_calls})", "llm_calls": self.llm_calls},
            )
            raise LLMCallLimitExceeded(
                f"Exceeded max LLM calls per run ({self.max_llm_calls}); used {self.llm_calls}"
            )


def default_deps(*, run_id: str | None = None, logs_dir: Path | str | None = None) -> Deps:
    """Build production deps with the real generator and a fresh Tracer."""
    settings = get_settings()
    tracer = Tracer(logs_dir=logs_dir or settings.logs_dir, run_id=run_id)
    return Deps(tracer=tracer)


async def run(
    utterance_text: str,
    *,
    entity_id: str | None = None,
    run_id: str | None = None,
    deps: Deps | None = None,
) -> RunState:
    """Execute the meeting-prep pipeline for one utterance."""
    active = deps or default_deps(run_id=run_id)
    run_id = active.tracer.run_id
    started = time.perf_counter()
    state = RunState(
        run_id=run_id,
        utterance=Utterance(
            text=utterance_text,
            ts=datetime.now(timezone.utc),
            mode="text",
        ),
        entity_id=entity_id,
    )

    with active.tracer.activate():
        try:
            state = await _pipeline(state, active)
        except LLMCallLimitExceeded as exc:
            state.status = "failed"
            state.question = str(exc)
            active.tracer.event("orchestrator", "failed", {"summary": str(exc)})
        except Exception as exc:  # noqa: BLE001 - surface failure on the run
            logger.exception("orchestrator failed run_id=%s", run_id)
            state.status = "failed"
            state.question = str(exc)
            active.tracer.event("orchestrator", "failed", {"summary": str(exc)})

        latency_ms = int((time.perf_counter() - started) * 1000)
        state.context["latency_ms"] = latency_ms
        state.context["llm_calls"] = active.llm_calls
        state.context["critic_rounds"] = state.round
        active.tracer.event(
            "orchestrator",
            "complete",
            {
                "summary": f"status={state.status} rounds={state.round} llm_calls={active.llm_calls}",
                "latency_ms": latency_ms,
                "status": state.status,
            },
        )
        _write_run_json(state, active.tracer)
    return state


async def _pipeline(state: RunState, deps: Deps) -> RunState:
    # 1. Router
    state = await router.run(state, deps)
    intent = state.intent
    assert intent is not None

    if intent.kind == "note":
        # TODO(step3.2): send the note to H-MEM via deps.hmem
        state.final_brief = Brief(person="", headline="Noted.", points=[], caveats=[])
        state.status = "done"
        deps.tracer.event("orchestrator", "note", {"summary": "Noted."})
        return state

    if intent.kind == "unknown":
        state.status = "needs_clarification"
        state.question = "Who are you meeting?"
        deps.tracer.event("orchestrator", "clarify", {"summary": state.question})
        return state

    # 2. Resolver stub
    state = _resolve_stub(state)
    if state.status == "needs_clarification":
        deps.tracer.event(
            "orchestrator",
            "clarify",
            {
                "summary": state.question or "needs clarification",
                "candidates": [c.model_dump() for c in state.candidates],
            },
        )
        return state

    # 3. Retriever stub
    state = _retrieve_stub(state)
    if not state.evidence:
        name = state.entity_name or "them"
        state.final_brief = Brief(
            person=name,
            headline=f"I don't have a brief for {name} yet.",
            points=[],
            caveats=["I couldn't find anything about them."],
        )
        state.status = "done"
        deps.tracer.event("orchestrator", "empty_evidence", {"summary": f"no evidence for {name}"})
        return state

    # 4–5. Writer / critic loop (max 2 critic rounds)
    state.round = 1
    state = await writer.run(state, deps)
    state = await critic.run(state, deps)
    deps.tracer.event(
        "orchestrator",
        "round",
        {"summary": f"round {state.round}: initial draft + critic", "round": state.round},
    )

    while _last_verdict(state).overall == "revise" and state.round < MAX_CRITIC_ROUNDS:
        prev_verdict = _last_verdict(state)
        state.round += 1
        state = await writer.run(state, deps)
        state = await critic.run(state, deps)
        deps.tracer.event(
            "orchestrator",
            "round",
            {
                "summary": _revision_summary(state.round, prev_verdict),
                "round": state.round,
            },
        )

    # 6. Final brief = last draft minus dropped points
    state.final_brief = _apply_drops(state.drafts[-1], _last_verdict(state))
    state.status = "done"

    # 7. Actions
    if intent.kind == "action":
        # TODO(step6): plan and optionally execute ActionPlan via tools
        brief = state.final_brief
        caveats = list(brief.caveats) + ["Actions not implemented yet."]
        state.final_brief = brief.model_copy(update={"caveats": caveats})

    return state


def _resolve_stub(state: RunState) -> RunState:
    """Temporary person resolution until world/hmem resolver exists.

    TODO(step3): replace with engines.world + hmem entity resolution.
    """
    if state.entity_id:
        resolved = _entity_from_id(state.entity_id)
        if resolved is not None:
            state.entity_id, state.entity_name = resolved
            return state

    name = (state.intent.person_name if state.intent else None) or ""
    name = name.strip()

    if name == "Karim":
        state.status = "needs_clarification"
        state.question = "Karim Haddad or Karim Saleh?"
        state.candidates = [
            Candidate(
                entity_id="karim-haddad",
                name="Karim Haddad",
                score=0.55,
                reason="Ledgerly founder in your messages",
            ),
            Candidate(
                entity_id="karim-saleh",
                name="Karim Saleh",
                score=0.45,
                reason="Appears in a class/finance thread",
            ),
        ]
        return state

    if name == "Karim Haddad":
        state.entity_id = "karim-haddad"
        state.entity_name = "Karim Haddad"
        return state

    if name == "Karim Saleh":
        state.entity_id = "karim-saleh"
        state.entity_name = "Karim Saleh"
        return state

    if not name:
        state.status = "needs_clarification"
        state.question = "Who are you meeting?"
        return state

    state.entity_id = name.lower().replace(" ", "-")
    state.entity_name = name
    return state


def _entity_from_id(entity_id: str) -> tuple[str, str] | None:
    key = entity_id.strip().lower().replace("_", "-").replace(" ", "-")
    known = {
        "karim-haddad": ("karim-haddad", "Karim Haddad"),
        "karim-saleh": ("karim-saleh", "Karim Saleh"),
    }
    if key in known:
        return known[key]
    if entity_id.strip() == "Karim Haddad":
        return ("karim-haddad", "Karim Haddad")
    return None


def _retrieve_stub(state: RunState) -> RunState:
    """Temporary evidence load until query_layer / connectors exist.

    TODO(step3): retrieve via deps.query_layer + world + hmem + web_scout.
    """
    name = state.entity_name or ""
    state.evidence = load_fixture_evidence(name)
    deps_tracer = Tracer.current()
    if deps_tracer is not None:
        deps_tracer.event(
            "retriever",
            "complete",
            {"summary": f"fixture evidence={len(state.evidence)} for {name!r}"},
        )
    return state


def _last_verdict(state: RunState) -> CriticVerdict:
    if not state.verdicts:
        raise RuntimeError("expected a critic verdict")
    return state.verdicts[-1]


def _revision_summary(round_no: int, prev: CriticVerdict) -> str:
    revised = sum(1 for item in prev.verdicts if item.decision == "fix")
    dropped = sum(1 for item in prev.verdicts if item.decision == "drop")
    return f"round {round_no}: writer revised {revised} points, dropped {dropped}"


def _apply_drops(draft: Brief, verdict: CriticVerdict) -> Brief:
    drop = {item.index for item in verdict.verdicts if item.decision == "drop"}
    points: list[TalkingPoint] = [
        point for index, point in enumerate(draft.points) if index not in drop
    ]
    return draft.model_copy(update={"points": points})


def _write_run_json(state: RunState, tracer: Tracer) -> None:
    path = tracer.path.parent / "run.json"
    path.write_text(state.model_dump_json(indent=2) + "\n", encoding="utf-8")
