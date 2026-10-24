"""Draft (or revise) a spoken meeting brief from evidence."""

from __future__ import annotations

import json
from typing import Any

from recall.agents.job_change import job_change_guidance
from recall.agents.prompts import BRIEF_STYLES, WRITER_SYSTEM
from recall.agents.time_context import with_today
from recall.llm.generator import generate_json
from recall.schemas import Brief, CriticVerdict, Evidence, RunState
from recall.tracing import Tracer


async def run(state: RunState, deps: Any) -> RunState:
    """Write a Brief from evidence and append it to state.drafts."""
    person = state.entity_name or (state.intent.person_name if state.intent else "unknown")
    prompt_parts = [
        "Use relative times anchored to TODAY (e.g. '3 weeks ago'), not exact calendar dates.",
        f"PERSON: {person}",
        f"UTTERANCE: {state.utterance.text}",
        job_change_guidance(state.evidence),
        "EVIDENCE:",
        format_evidence_list(state.evidence),
    ]
    rel_label = state.context.get("relationship_label")
    rel_conf = state.context.get("relationship_confidence")
    rel_style = state.context.get("relationship_style")
    if rel_label:
        prompt_parts.append(
            f"RELATIONSHIP: {rel_label} ({rel_conf if rel_conf is not None else '?'})"
        )
        style = rel_style or BRIEF_STYLES.get(str(rel_label), "")
        if style:
            prompt_parts.append(f"STYLE: {style}")
    if state.context.get("meal_hook"):
        prompt_parts.append(
            "MEAL_HOOK: Add one caveat only: Ask me where to eat if you need ideas."
        )
    if state.context.get("tentative_identity_evidence") or any(
        "tentative_identity" in (e.tags or []) for e in state.evidence.values()
    ):
        prompt_parts.append(
            "Evidence tagged tentative_identity may belong to someone else: "
            "say 'probably' or leave it out."
        )
    feedback = _critic_feedback(state)
    if feedback:
        prompt_parts.extend(["CRITIC_FEEDBACK:", feedback])

    prompt = with_today("\n".join(prompt_parts))
    generate_json_fn = getattr(deps, "generate_json", None) or generate_json
    brief = await generate_json_fn(prompt, WRITER_SYSTEM, Brief, role="writer")
    assert isinstance(brief, Brief)
    # Sample-mode cache often returns a default brief for another person; retarget.
    if person and brief.person and brief.person != person:
        headline = brief.headline.replace(brief.person, person)
        if person not in headline:
            headline = f"{person}. {headline}"
        brief = brief.model_copy(update={"person": person, "headline": headline})
    state.drafts.append(brief)
    summary = (
        f"person={brief.person!r} points={len(brief.points)} "
        f"headline={brief.headline[:80]!r}"
    )
    _log(deps, "writer", summary, {"brief": brief.model_dump(mode="json")})
    return state


def format_evidence_list(evidence: dict[str, Evidence]) -> str:
    """Compact evidence lines for writer/critic prompts.

    Example with tags: ``[e13] linkedin 2026-10-09 (job_change): text``.
    """
    if not evidence:
        return "(none)"
    lines: list[str] = []
    for item in sorted(evidence.values(), key=lambda e: (e.ts, e.id), reverse=True):
        day = item.ts.date().isoformat()
        text = " ".join(item.text.split())
        tags = f" ({', '.join(item.tags)})" if item.tags else ""
        lines.append(f"[{item.id}] {item.source} {day}{tags}: {text}")
    return "\n".join(lines)


def _critic_feedback(state: RunState) -> str | None:
    if not state.verdicts:
        return None
    last: CriticVerdict = state.verdicts[-1]
    if last.overall == "pass":
        return None
    return json.dumps(last.model_dump(mode="json"), indent=2)


def _log(deps: Any, agent: str, summary: str, payload: dict[str, Any]) -> None:
    tracer = getattr(deps, "tracer", None) or Tracer.current()
    if tracer is not None:
        tracer.event(agent, "complete", {"summary": summary, **payload})
