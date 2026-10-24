"""In-memory ask sessions, speech text, and timed orchestrator calls."""

from __future__ import annotations

import asyncio
import inspect
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from recall.schemas import Brief, RunState, TalkingPoint, Utterance

_RUN_TTL_S = 10 * 60
_ASK_SOFT_TIMEOUT_S = 18.0
_MAX_SPEECH_WORDS = 90


@dataclass
class StoredRun:
    """One in-progress / recent ask run kept for phone clarification."""

    state: RunState
    expires_at: float


_runs: dict[str, StoredRun] = {}


def clear_runs() -> None:
    """Drop all stored runs (tests)."""
    _runs.clear()


def _purge() -> None:
    now = time.monotonic()
    dead = [rid for rid, item in _runs.items() if item.expires_at <= now]
    for rid in dead:
        _runs.pop(rid, None)


def store_run(state: RunState) -> None:
    """Remember a run for clarification / confirm follow-ups."""
    _purge()
    _runs[state.run_id] = StoredRun(
        state=state,
        expires_at=time.monotonic() + _RUN_TTL_S,
    )


def get_run(run_id: str) -> RunState | None:
    """Return a non-expired stored run, if any."""
    _purge()
    item = _runs.get(run_id)
    if item is None:
        return None
    return item.state


def build_speech(state: RunState) -> str:
    """Plain spoken line: headline + points, no markdown/ids, ≤90 words."""
    if state.status == "needs_clarification" and state.question:
        return _trim_words(_strip_markup(state.question), _MAX_SPEECH_WORDS)
    if state.status == "awaiting_confirmation" and state.action_plan:
        summary = state.action_plan.spoken_summary or "Confirm these actions?"
        return _trim_words(_strip_markup(summary), _MAX_SPEECH_WORDS)
    brief = state.final_brief
    if brief is None and state.drafts:
        brief = state.drafts[-1]
    if brief is None:
        return "I don't have a brief yet."
    parts = [brief.headline, *[p.text for p in brief.points]]
    joined = ". ".join(_strip_markup(p).strip().rstrip(".") for p in parts if p and p.strip())
    return _trim_words(joined, _MAX_SPEECH_WORDS) or "Brief ready."


def enrich_ask_payload(state: RunState) -> dict[str, Any]:
    """RunState JSON plus spoken ``speech`` for phone / dashboard TTS."""
    store_run(state)
    data = state.model_dump(mode="json")
    data["speech"] = build_speech(state)
    data["run_id"] = state.run_id
    data["status"] = state.status
    return data


def timeout_brief(state: RunState | None, *, text: str, run_id: str | None) -> RunState:
    """Best-effort done brief when the orchestrator hits the soft timeout."""
    if state is not None and (state.final_brief or state.drafts):
        brief = state.final_brief or state.drafts[-1]
        caveats = list(brief.caveats)
        if "Answer truncated after 18s time budget." not in caveats:
            caveats.append("Answer truncated after 18s time budget.")
        out = state.model_copy(
            update={
                "status": "done",
                "final_brief": brief.model_copy(update={"caveats": caveats}),
            }
        )
        return out
    rid = run_id or (state.run_id if state else "timeout")
    brief = Brief(
        person="unknown",
        headline="Still working on that",
        points=[
            TalkingPoint(
                text="Try again in a moment for the full brief.",
                evidence_ids=[],
                kind="logistics",
                priority=1,
            )
        ],
        caveats=["Answer truncated after 18s time budget."],
    )
    return RunState(
        run_id=rid,
        utterance=Utterance(
            text=text,
            ts=datetime.now(timezone.utc),
            mode="text",
        ),
        status="done",
        final_brief=brief,
    )


async def run_ask(
    text: str,
    *,
    run_id: str | None = None,
    entity_id: str | None = None,
) -> RunState:
    """Call the public orchestrator with soft 18s timeout and run continuation."""
    from recall.orchestrator import run as orchestrate

    cleaned = text.strip()
    prior = get_run(run_id) if run_id else None

    if prior is not None and prior.status == "awaiting_confirmation":
        return await _handle_confirmation(prior, cleaned)

    kwargs: dict[str, Any] = {}
    if entity_id:
        kwargs["entity_id"] = entity_id
    if run_id and prior is not None and prior.status == "needs_clarification":
        kwargs["run_id"] = run_id
        # Orchestrator treats utterance as clarification when resuming.
    elif run_id:
        kwargs["run_id"] = run_id

    coro = orchestrate(cleaned, **kwargs)
    if not inspect.isawaitable(coro):
        return coro  # type: ignore[return-value]

    task = asyncio.create_task(coro)
    try:
        return await asyncio.wait_for(asyncio.shield(task), timeout=_ASK_SOFT_TIMEOUT_S)
    except asyncio.TimeoutError:
        # Let the task finish in the background; return a timed-out brief now.
        partial = get_run(run_id) if run_id else None
        return timeout_brief(partial, text=cleaned, run_id=run_id)
    except asyncio.CancelledError:
        task.cancel()
        raise


async def _handle_confirmation(prior: RunState, text: str) -> RunState:
    """Map confirm/cancel speech onto the actions module when present."""
    from recall.agents import actions

    lowered = text.strip().lower()
    confirmed = lowered in {"confirm", "yes", "ok", "okay", "do it", "go ahead"}
    cancelled = lowered in {"cancel", "no", "stop", "never mind", "nevermind"}
    if not confirmed and not cancelled:
        prior.status = "awaiting_confirmation"
        prior.question = prior.question or "Say confirm or cancel."
        return prior

    confirm_fn = getattr(actions, "confirm", None)
    execute_fn = getattr(actions, "execute", None)
    fn = confirm_fn if callable(confirm_fn) else execute_fn if callable(execute_fn) else None
    if fn is not None:
        result = fn(run_id=prior.run_id, confirmed=confirmed)
        if inspect.isawaitable(result):
            await result
    prior.status = "done"
    if prior.action_plan is not None and cancelled:
        prior.action_plan = None
        prior.question = "Actions cancelled."
    elif confirmed:
        prior.question = None
    return prior


def _strip_markup(text: str) -> str:
    cleaned = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    cleaned = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", cleaned)
    cleaned = re.sub(r"[#*_`]+", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _trim_words(text: str, limit: int) -> str:
    words = text.split()
    if len(words) <= limit:
        return text.strip()
    return " ".join(words[:limit]).rstrip(".,;:") + "."


@dataclass
class IngestStatus:
    """Last background / manual live ingest snapshot for /api/status."""

    last_ingest_at: str | None = None
    per_source: dict[str, Any] = field(default_factory=dict)
    totals: dict[str, int] = field(default_factory=dict)


ingest_status = IngestStatus()
