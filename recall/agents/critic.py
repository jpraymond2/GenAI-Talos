"""Judge a draft brief against evidence (fast Nemotron via ROLE_MODELS)."""

from __future__ import annotations

import json
from typing import Any

from recall.agents.job_change import job_change_guidance, matching_ids, trap_ids
from recall.agents.prompts import CRITIC_SYSTEM
from recall.agents.time_context import with_today
from recall.agents.writer import format_evidence_list
from recall.llm.generator import generate_json
from recall.schemas import Brief, CriticVerdict, Evidence, PointVerdict, RunState
from recall.tracing import Tracer


async def run(state: RunState, deps: Any) -> RunState:
    """Critique the latest draft brief and append CriticVerdict to state."""
    if not state.drafts:
        raise ValueError("critic requires at least one draft brief on state.drafts")

    draft = state.drafts[-1]
    parts = [
        "Judge staleness relative to TODAY. Prefer newer evidence when sources disagree.",
        job_change_guidance(state.evidence),
        "DRAFT_BRIEF:",
        json.dumps(draft.model_dump(mode="json"), indent=2),
        "EVIDENCE:",
        format_evidence_list(state.evidence),
    ]
    rel_label = state.context.get("relationship_label")
    if rel_label:
        parts.append(
            f"RELATIONSHIP: {rel_label} "
            f"({state.context.get('relationship_confidence', '?')})"
        )
        style = state.context.get("relationship_style")
        if style:
            parts.append(f"STYLE: {style}")
    if state.context.get("suggest_result"):
        parts.append("SUGGEST: treat this draft as place suggestions; enforce citation rules.")
    prompt = with_today("\n".join(parts))
    generate_json_fn = getattr(deps, "generate_json", None) or generate_json
    verdict = await generate_json_fn(prompt, CRITIC_SYSTEM, CriticVerdict, role="critic")
    assert isinstance(verdict, CriticVerdict)
    verdict = _enforce_job_change_rules(verdict, draft, state.evidence)
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


def _enforce_job_change_rules(
    verdict: CriticVerdict,
    draft: Brief,
    evidence: dict[str, Evidence],
) -> CriticVerdict:
    """Hard-drop mismatched LinkedIn job_changes; require matching congrats."""
    traps = trap_ids(evidence)
    matches = matching_ids(evidence)
    by_index = {item.index: item for item in verdict.verdicts}
    missing = list(verdict.missing)
    overall = verdict.overall
    changed = False

    for index, point in enumerate(draft.points):
        if not any(eid in traps for eid in point.evidence_ids):
            continue
        by_index[index] = PointVerdict(
            index=index,
            decision="drop",
            reason="LinkedIn job_change company does not match other evidence.",
            fix_hint="Remove this citation; use only matching job_change ids.",
        )
        overall = "revise"
        changed = True

    cited_match = any(
        any(eid in matches for eid in point.evidence_ids)
        and not any(eid in traps for eid in point.evidence_ids)
        for point in draft.points
    )
    if matches and not cited_match:
        hint = f"Add a congratulation citing {matches[0]} only."
        if hint not in missing:
            missing.append(hint)
        overall = "revise"
        changed = True

    if not changed:
        return verdict

    return verdict.model_copy(
        update={
            "overall": overall,
            "verdicts": sorted(by_index.values(), key=lambda v: v.index),
            "missing": missing,
        }
    )


def _log(deps: Any, agent: str, summary: str, payload: dict[str, Any]) -> None:
    tracer = getattr(deps, "tracer", None) or Tracer.current()
    if tracer is not None:
        tracer.event(agent, "complete", {"summary": summary, **payload})
