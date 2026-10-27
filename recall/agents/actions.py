"""Propose and (optionally) execute side effects. Sample mode dry-runs only."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from recall.config import Settings, get_settings
from recall.schemas import ActionPlan, ActionStep, Brief, Evidence, RunState
from recall.tracing import Tracer

logger = logging.getLogger(__name__)


async def plan(state: RunState, deps: Any = None) -> ActionPlan:
    """Propose up to 3 actions from the brief + evidence pack."""
    brief = state.final_brief or (state.drafts[-1] if state.drafts else None)
    steps: list[ActionStep] = []
    pack = state.evidence or {}

    open_loop = _oldest_owner_open_loop(pack)
    if open_loop is not None:
        who = state.entity_name or (brief.person if brief else "them")
        steps.append(
            ActionStep(
                tool="gmail.draft",
                args={
                    "to_entity": who,
                    "evidence_id": open_loop.id,
                    "subject": "Re: follow-up",
                    "body_preview": _draft_body(open_loop, who),
                },
                summary=f"Draft a Gmail reply for the open loop ({open_loop.id})",
            )
        )

    when = state.intent.when if state.intent else None
    has_upcoming = any(
        e.source == "calendar" and "upcoming" in (e.tags or []) for e in pack.values()
    )
    if when and not has_upcoming and state.entity_name:
        steps.append(
            ActionStep(
                tool="calendar.hold",
                args={
                    "title": f"Hold: {state.entity_name}",
                    "when": when.isoformat() if hasattr(when, "isoformat") else str(when),
                    "entity_name": state.entity_name,
                },
                summary=f"Hold calendar time for {state.entity_name}",
            )
        )

    if state.context.get("meal_hook") or (
        state.intent and state.intent.kind == "suggest"
    ):
        place = str(state.context.get("suggested_place") or "a nearby spot")
        steps.append(
            ActionStep(
                tool="suggester.restaurant",
                args={"place_or_cuisine": place},
                summary=f"Note restaurant suggestion: {place}",
            )
        )

    steps = steps[:3]
    if not steps:
        spoken = "No actions to propose."
    elif len(steps) == 1:
        spoken = f"I can {steps[0].summary.lower()}."
    else:
        spoken = (
            "I can "
            + "; ".join(s.summary.lower() for s in steps[:-1])
            + f"; and {steps[-1].summary.lower()}."
        )

    plan_out = ActionPlan(
        steps=steps,
        spoken_summary=spoken,
        requires_confirmation=True,
    )
    tracer = getattr(deps, "tracer", None) if deps is not None else Tracer.current()
    if tracer is not None:
        tracer.event(
            "actions",
            "plan",
            {
                "summary": f"steps={len(steps)}",
                "tools": [s.tool for s in steps],
            },
        )
    return plan_out


async def execute(
    *,
    run_id: str,
    confirmed: bool,
    action_ids: list[str] | None = None,
    deps: Any = None,
    state: RunState | None = None,
) -> dict[str, Any]:
    """Dry-run in sample mode; live Google writes return ``not_enabled``."""
    settings: Settings = (
        getattr(deps, "settings", None) if deps is not None else None
    ) or get_settings()
    tracer = getattr(deps, "tracer", None) if deps is not None else Tracer.current()

    if not confirmed:
        if tracer is not None:
            tracer.event(
                "actions",
                "cancelled",
                {"summary": "actions cancelled", "run_id": run_id},
            )
        return {"status": "cancelled", "run_id": run_id}

    if not settings.sample_mode:
        if tracer is not None:
            tracer.event(
                "actions",
                "not_enabled",
                {"summary": "live Google writes not_enabled", "run_id": run_id},
            )
        return {
            "status": "not_enabled",
            "run_id": run_id,
            "message": "Live Google writes are not enabled; use SAMPLE_MODE dry-run.",
        }

    plan_obj = state.action_plan if state is not None else None
    if plan_obj is None and state is None:
        # Best-effort: load from logs/<run_id>/state.json
        plan_obj = _load_plan(run_id, settings)

    steps = list(plan_obj.steps) if plan_obj is not None else []
    if action_ids:
        want = set(action_ids)
        steps = [
            s
            for i, s in enumerate(steps)
            if s.tool in want or str(i) in want or f"step-{i}" in want
        ]

    previews: list[dict[str, Any]] = []
    for step in steps:
        if step.tool == "gmail.draft":
            previews.append(
                {
                    "tool": step.tool,
                    "status": "dry_run",
                    "preview": {
                        "type": "gmail_draft",
                        "to": step.args.get("to_entity"),
                        "subject": step.args.get("subject"),
                        "body": step.args.get("body_preview"),
                        "evidence_id": step.args.get("evidence_id"),
                    },
                }
            )
        elif step.tool == "calendar.hold":
            previews.append(
                {
                    "tool": step.tool,
                    "status": "dry_run",
                    "preview": {
                        "type": "calendar_hold",
                        "title": step.args.get("title"),
                        "when": step.args.get("when"),
                        "entity_name": step.args.get("entity_name"),
                    },
                }
            )
        else:
            previews.append(
                {
                    "tool": step.tool,
                    "status": "dry_run",
                    "preview": {"type": step.tool, "args": step.args},
                }
            )

    out_dir = Path(settings.logs_dir) / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "run_id": run_id,
        "status": "dry_run",
        "previews": previews,
    }
    (out_dir / "actions_dryrun.json").write_text(
        json.dumps(payload, indent=2),
        encoding="utf-8",
    )
    if tracer is not None:
        tracer.event(
            "actions",
            "dry_run",
            {
                "summary": f"dry_run steps={len(previews)}",
                "path": str(out_dir / "actions_dryrun.json"),
                "tools": [p["tool"] for p in previews],
            },
        )
    return payload


def _oldest_owner_open_loop(pack: dict[str, Evidence]) -> Evidence | None:
    loops = [
        e
        for e in pack.values()
        if "open_loop" in (e.tags or []) or "promise" in (e.tags or [])
    ]
    if not loops:
        return None
    return min(loops, key=lambda e: e.ts)


def _draft_body(item: Evidence, who: str) -> str:
    quote = (item.text or "").strip().replace("\n", " ")
    if len(quote) > 180:
        quote = quote[:177] + "..."
    return (
        f"Hi {who.split()[0] if who else 'there'},\n\n"
        f"Following up on: {quote}\n\n"
        f"Best,\n"
    )


def _load_plan(run_id: str, settings: Settings) -> ActionPlan | None:
    path = Path(settings.logs_dir) / run_id / "state.json"
    if not path.is_file():
        return None
    try:
        state = RunState.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    return state.action_plan
