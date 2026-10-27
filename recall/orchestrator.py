"""Run one meeting-prep pass end-to-end.

Order: router -> resolve -> retrieve (parallel) -> writer/critic loop.
Uses World Model + H-MEM engines; no fixture or person-name stubs.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel
from rapidfuzz import fuzz

from recall.agents import critic, router, writer
from recall.agents import suggester
from recall.agents.job_change import sanitize_brief_job_changes
from recall.agents.prompts import BRIEF_STYLES
from recall.config import Settings, get_settings
from recall.engines.world.preferences import get_preferences
from recall.engines.world.relationship import classify_relationship
from recall.data.query_layer import QueryLayer, get_query_layer
from recall.engines.hmem.ledger import TrustLedgerService
from recall.engines.hmem.mining import MemoryMiningService
from recall.engines.hmem.models import MineEvent
from recall.engines.hmem.retrieval import MemoryRetrievalService
from recall.engines.hmem.store import MemoryStoreService, hmem_db_path
from recall.engines.world.context_builder import ContextBuilderService
from recall.engines.world.identity_service import IdentityService
from recall.engines.world.ingest import IngestPipelineService
from recall.engines.world.store import (
    WorldStore,
    source_tables_fingerprint,
    world_db_path,
)
from recall.llm import generator as llm
from recall.schemas import Brief, Candidate, CriticVerdict, Evidence, RunState, TalkingPoint, Utterance
from recall.tracing import Tracer

logger = logging.getLogger(__name__)

MAX_CRITIC_ROUNDS = 2
MAX_LLM_CALLS = 8
RETRIEVE_TIMEOUT_S = 15.0
RESOLVE_MIN_SCORE = 0.6
RESOLVE_MARGIN = 0.1
EVIDENCE_CAP = 30

GenerateFn = Callable[..., Awaitable[str]]
GenerateJsonFn = Callable[..., Awaitable[BaseModel]]

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.\w+")
_PHONE_RE = re.compile(r"\+?\d[\d\s().-]{8,}\d")
_ORDINAL_RE = re.compile(
    r"(?i)\b(?:the\s+)?(first|1st|1|second|2nd|2|third|3rd|3)\b"
)


class LLMCallLimitExceeded(RuntimeError):
    """Raised when a run exceeds MAX_LLM_CALLS."""


@dataclass
class Deps:
    """Injected dependencies for agents and the orchestrator."""

    tracer: Tracer
    settings: Settings
    query_layer: QueryLayer
    world_store: WorldStore
    world: ContextBuilderService
    hmem_store: MemoryStoreService
    hmem_retrieval: MemoryRetrievalService
    hmem_mining: MemoryMiningService
    hmem_ledger: TrustLedgerService
    identity: IdentityService
    generate: GenerateFn = field(default=llm.generate)
    generate_json: GenerateJsonFn = field(default=llm.generate_json)
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
                {
                    "summary": f"LLM call limit exceeded ({self.max_llm_calls})",
                    "llm_calls": self.llm_calls,
                },
            )
            raise LLMCallLimitExceeded(
                f"Exceeded max LLM calls per run ({self.max_llm_calls}); used {self.llm_calls}"
            )


def build_deps(
    sample_mode: bool | None = None,
    *,
    run_id: str | None = None,
    logs_dir: Path | str | None = None,
    generate: GenerateFn | None = None,
    generate_json: GenerateJsonFn | None = None,
) -> Deps:
    """Build production deps once; ingest world DB if empty."""
    if sample_mode is True:
        import os

        os.environ["SAMPLE_MODE"] = "true"
        get_settings.cache_clear()
    elif sample_mode is False:
        import os

        os.environ["SAMPLE_MODE"] = "false"
        get_settings.cache_clear()

    settings = get_settings()
    tracer = Tracer(logs_dir=logs_dir or settings.logs_dir, run_id=run_id)
    layer = get_query_layer(settings, tracer=tracer)
    db_path = world_db_path(settings)
    world_store = WorldStore(path=db_path, settings=settings)
    expected_fp = (
        source_tables_fingerprint(settings.sample_data_dir) if settings.sample_mode else None
    )
    stored_fp = world_store.get_meta("source_fingerprint")
    empty = world_store.count_entities() == 0
    fingerprint_mismatch = bool(expected_fp) and stored_fp != expected_fp
    if empty or fingerprint_mismatch:
        reason = "empty" if empty else "fingerprint_mismatch"
        tracer.event(
            "orchestrator",
            "world_reingest",
            {
                "reason": reason,
                "fingerprint": expected_fp,
                "prior_fingerprint": stored_fp,
                "summary": f"world reingest ({reason})",
            },
        )
        if fingerprint_mismatch and not empty:
            world_store.close()
            db_path.unlink(missing_ok=True)
            for suffix in ("-wal", "-shm"):
                Path(str(db_path) + suffix).unlink(missing_ok=True)
            world_store = WorldStore(path=db_path, settings=settings)
        IngestPipelineService(store=world_store, settings=settings, tracer=tracer).run(layer)
        if expected_fp:
            world_store.set_meta("source_fingerprint", expected_fp)

    world = ContextBuilderService(
        store=world_store, settings=settings, query_layer=layer, tracer=tracer
    )
    hmem_store = MemoryStoreService(path=hmem_db_path(settings), settings=settings)
    hmem_ledger = TrustLedgerService(
        settings=settings, tracer=tracer, run_id=tracer.run_id
    )
    hmem_retrieval = MemoryRetrievalService(
        store=hmem_store, settings=settings, tracer=tracer, ledger=hmem_ledger
    )
    hmem_mining = MemoryMiningService(
        store=hmem_store,
        ledger=hmem_ledger,
        settings=settings,
        tracer=tracer,
        world_store=world_store,
    )
    identity = IdentityService(
        store=world_store, settings=settings, tracer=tracer, query_layer=layer
    )
    return Deps(
        tracer=tracer,
        settings=settings,
        query_layer=layer,
        world_store=world_store,
        world=world,
        hmem_store=hmem_store,
        hmem_retrieval=hmem_retrieval,
        hmem_mining=hmem_mining,
        hmem_ledger=hmem_ledger,
        identity=identity,
        generate=generate or llm.generate,
        generate_json=generate_json or llm.generate_json,
    )


def default_deps(*, run_id: str | None = None, logs_dir: Path | str | None = None) -> Deps:
    """Alias for build_deps (backward compatible)."""
    return build_deps(run_id=run_id, logs_dir=logs_dir)


def _state_path(tracer: Tracer) -> Path:
    return tracer.path.parent / "state.json"


def _save_state(state: RunState, tracer: Tracer) -> None:
    path = _state_path(tracer)
    path.write_text(
        state.model_dump_json(indent=2),
        encoding="utf-8",
    )


def _load_state(run_id: str, logs_dir: Path | str) -> RunState | None:
    path = Path(logs_dir) / run_id / "state.json"
    if not path.exists():
        return None
    return RunState.model_validate_json(path.read_text(encoding="utf-8"))


async def run(
    utterance_text: str,
    *,
    entity_id: str | None = None,
    run_id: str | None = None,
    deps: Deps | None = None,
    clarification_reply: str | None = None,
    force_kind: Literal["brief", "note", "action", "question"] | None = None,
) -> RunState:
    """Execute the meeting-prep pipeline for one utterance."""
    # Continuation: resume a prior needs_clarification run.
    if run_id and clarification_reply is None and deps is None:
        # Treat utterance as the clarification answer when resuming.
        settings = get_settings()
        prior = _load_state(run_id, settings.logs_dir)
        if prior is not None and prior.status == "needs_clarification" and prior.candidates:
            clarification_reply = utterance_text
            active = build_deps(run_id=run_id)
            return await _continue_from_clarification(prior, clarification_reply, active)

    resume_run_id = run_id
    active = deps or build_deps(run_id=run_id)
    run_id = active.tracer.run_id
    started = time.perf_counter()

    if clarification_reply and resume_run_id:
        prior = _load_state(resume_run_id, active.settings.logs_dir)
        if prior is not None and prior.candidates:
            # Keep tracing under the original run folder when possible.
            if active.tracer.run_id != resume_run_id:
                active.tracer = Tracer(
                    logs_dir=active.settings.logs_dir, run_id=resume_run_id
                )
            return await _continue_from_clarification(prior, clarification_reply, active)

    state = RunState(
        run_id=run_id,
        utterance=Utterance(
            text=utterance_text,
            ts=datetime.now(timezone.utc),
            mode="text",
        ),
        entity_id=entity_id,
    )
    if force_kind:
        state.context["force_kind"] = force_kind

    with active.tracer.activate():
        try:
            state = await _pipeline(state, active)
        except LLMCallLimitExceeded as exc:
            state.status = "failed"
            state.question = str(exc)
        finally:
            state.context["latency_ms"] = int((time.perf_counter() - started) * 1000)
            state.context["llm_calls"] = active.llm_calls
            state.context["critic_rounds"] = state.round
            if state.status == "needs_clarification":
                _save_state(state, active.tracer)
            active.tracer.event(
                "orchestrator",
                "final",
                {
                    "summary": (
                        f"status={state.status} points="
                        f"{len(state.final_brief.points) if state.final_brief else 0} "
                        f"caveats="
                        f"{len(state.final_brief.caveats) if state.final_brief else 0} "
                        f"latency_ms={state.context.get('latency_ms')} "
                        f"llm_calls={active.llm_calls}"
                    ),
                    "status": state.status,
                    "points": len(state.final_brief.points) if state.final_brief else 0,
                    "caveats": list(state.final_brief.caveats) if state.final_brief else [],
                    "latency_ms": state.context.get("latency_ms"),
                    "llm_calls": active.llm_calls,
                },
            )
    return state


async def _continue_from_clarification(
    prior: RunState,
    reply: str,
    deps: Deps,
) -> RunState:
    """Match clarification reply to a candidate and continue from retrieval."""
    started = time.perf_counter()
    matched = _match_clarification_reply(reply, prior.candidates)
    if matched is None:
        prior.status = "needs_clarification"
        prior.question = prior.question or "Which one?"
        _save_state(prior, deps.tracer)
        return prior
    prior.entity_id = matched.entity_id
    prior.entity_name = matched.name
    prior.status = "running"
    prior.question = None
    with deps.tracer.activate():
        try:
            if prior.intent and prior.intent.kind == "note":
                prior = await _handle_note(prior, deps)
            else:
                prior = await _pipeline_from_retrieve(prior, deps)
        finally:
            prior.context["latency_ms"] = int((time.perf_counter() - started) * 1000)
            prior.context["llm_calls"] = deps.llm_calls
            prior.context["critic_rounds"] = prior.round
            deps.tracer.event(
                "orchestrator",
                "final",
                {
                    "summary": f"status={prior.status} (continued)",
                    "status": prior.status,
                    "latency_ms": prior.context.get("latency_ms"),
                    "llm_calls": deps.llm_calls,
                },
            )
    return prior


async def _pipeline(state: RunState, deps: Deps) -> RunState:
    """Router → resolve → (note | retrieve → writer/critic)."""
    state = await router.run(state, deps)
    intent = state.intent
    assert intent is not None
    force_kind = state.context.get("force_kind")
    if force_kind and force_kind != intent.kind:
        state.intent = intent.model_copy(update={"kind": force_kind})
        intent = state.intent

    if intent.kind == "unknown" and intent.confidence < 0.4:
        state.status = "needs_clarification"
        state.question = "Who are you meeting?"
        deps.tracer.event("orchestrator", "clarify", {"summary": state.question})
        return state

    state = _resolve(state, deps)
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

    if intent.kind == "note":
        return await _handle_note(state, deps)

    # Sample-mode router may miss "suggest"; detect meal/place asks from text.
    if intent.kind != "suggest" and _looks_like_suggest(state.utterance.text):
        state.intent = intent.model_copy(update={"kind": "suggest"})
        intent = state.intent

    if intent.kind == "suggest":
        return await suggester.run(state, deps)

    return await _pipeline_from_retrieve(state, deps)


async def _pipeline_from_retrieve(state: RunState, deps: Deps) -> RunState:
    """Retrieve → evidence pack → writer/critic → feedback."""
    state = _attach_relationship(state, deps)
    state = await _retrieve(state, deps)
    if not state.evidence:
        name = state.entity_name or "them"
        state.final_brief = Brief(
            person=name,
            headline=f"I don't have a brief for {name} yet.",
            points=[],
            caveats=["I couldn't find anything about them."],
        )
        state.status = "done"
        deps.tracer.event(
            "orchestrator",
            "empty_evidence",
            {"summary": f"no evidence for {name}"},
        )
        return state

    state = _maybe_meal_hook(state, deps)

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

    brief = sanitize_brief_job_changes(
        _apply_drops(state.drafts[-1], _last_verdict(state)),
        state.evidence,
    )
    extra_caveats = list(state.context.get("retrieve_caveats") or [])
    if state.context.get("meal_hook"):
        hook = "Ask me where to eat if you need ideas"
        if hook.lower() not in " ".join(brief.caveats).lower():
            extra_caveats.append(hook)
    if extra_caveats:
        brief = brief.model_copy(
            update={"caveats": list(brief.caveats) + extra_caveats}
        )
    state.final_brief = brief
    state.status = "done"
    _apply_memory_feedback(state, deps)

    if state.intent and state.intent.kind == "action":
        # TODO(step6): plan and optionally execute ActionPlan via tools;
        # then hmem.mine({type: "action_outcome", ...}).
        brief = state.final_brief
        caveats = list(brief.caveats) + ["Actions not implemented yet."]
        state.final_brief = brief.model_copy(update={"caveats": caveats})

    return state


def _resolve(state: RunState, deps: Deps) -> RunState:
    """Resolve person via handle (email/phone) or world.resolve(name)."""
    if state.entity_id:
        entity = deps.world_store.get_entity(state.entity_id)
        if entity is not None:
            state.entity_name = entity.canonical_name
            deps.tracer.event(
                "orchestrator",
                "resolve",
                {
                    "summary": f"entity_id={state.entity_id}",
                    "candidates": [
                        {
                            "entity_id": entity.id,
                            "name": entity.canonical_name,
                            "score": 1.0,
                        }
                    ],
                },
            )
            return state

    utterance = state.utterance.text
    name = (state.intent.person_name if state.intent else None) or ""
    name = name.strip()
    # Prefer a proper name from the utterance when the router kept only a first
    # name, or named a different person than the one spoken in the utterance.
    uttered = _extract_person_name_from_text(utterance)
    if uttered:
        if not name:
            name = uttered
        elif name.lower() in uttered.lower() and len(uttered.split()) > len(name.split()):
            name = uttered
        elif name.lower() not in uttered.lower() and uttered.lower() not in name.lower():
            name = uttered

    handle = _extract_handle(utterance) or _extract_handle(name)
    if handle:
        hit = deps.identity.resolve_handle(handle)
        if hit and hit.get("entity_id") and hit.get("status") in {
            "anchor",
            "active",
            "confirmed_by_user",
            "assumed",
        }:
            # Prefer confirmed/active/anchor; tentative still usable with tag later.
            if hit.get("decision") in {"auto_linked", "tentative"} or hit.get("status") == "anchor":
                state.entity_id = str(hit["entity_id"])
                state.entity_name = str(hit.get("name") or handle)
                state.context["identity_probability"] = hit.get("probability")
                state.context["identity_status"] = hit.get("status")
                if hit.get("decision") == "tentative" or hit.get("status") == "assumed":
                    state.context["tentative_identity"] = True
                deps.tracer.event(
                    "orchestrator",
                    "resolve",
                    {
                        "summary": f"handle={handle} -> {state.entity_name}",
                        "candidates": [
                            {
                                "entity_id": state.entity_id,
                                "name": state.entity_name,
                                "score": float(hit.get("probability") or 1.0),
                            }
                        ],
                    },
                )
                return state
        if hit and hit.get("decision") == "needs_user":
            # Fall through to name resolve for candidates when possible.
            pass

    if not name:
        state.status = "needs_clarification"
        state.question = "Who are you meeting?"
        return state

    ranked = deps.world.resolve(name)
    candidates = [
        Candidate(
            entity_id=c.entity_id,
            name=c.name,
            score=c.score,
            reason=c.reason,
        )
        for c in ranked
    ]
    deps.tracer.event(
        "orchestrator",
        "resolve",
        {
            "summary": f"name={name!r} candidates={len(candidates)}",
            "candidates": [c.model_dump() for c in candidates],
        },
    )

    if not candidates:
        state.status = "needs_clarification"
        state.question = f"I don't know anyone called {name}. Who is it?"
        state.candidates = []
        return state

    top = candidates[0]
    second = candidates[1] if len(candidates) > 1 else None
    # Multi-token exact match wins even if weaker first-name partials sit nearby.
    multi_exact = (
        top.score >= RESOLVE_MIN_SCORE
        and len(name.split()) >= 2
        and top.name.lower() == name.lower()
    )
    margin_clear = second is None or (top.score - second.score) >= RESOLVE_MARGIN
    ambiguous = top.score < RESOLVE_MIN_SCORE or not (multi_exact or margin_clear)
    if ambiguous:
        shown = _clarification_candidates(candidates, limit=3)
        state.status = "needs_clarification"
        state.candidates = shown
        if len(shown) == 1:
            state.question = f"Did you mean {shown[0].name}?"
        else:
            names = " or ".join(c.name for c in shown)
            state.question = f"{names}?"
        return state

    state.entity_id = top.entity_id
    state.entity_name = top.name
    state.candidates = candidates
    return state


_MEAL_RE = re.compile(
    r"(?i)\b(lunch|dinner|brunch|coffee|drinks|eat|restaurant|cafe|café)\b"
)
_SUGGEST_RE = re.compile(
    r"(?i)\b(where should|what does .+ like|suggest (a )?place|"
    r"where (?:to|should we|can we)|lunch with|dinner with|coffee with)\b"
)


def _looks_like_suggest(text: str) -> bool:
    return bool(_SUGGEST_RE.search(text or ""))


def _attach_relationship(state: RunState, deps: Deps) -> RunState:
    """Load relationship label/style into state.context for the writer."""
    if not state.entity_id:
        return state
    try:
        assessment = classify_relationship(
            state.entity_id,
            store=deps.world_store,
            settings=deps.settings,
            query_layer=deps.query_layer,
            tracer=deps.tracer,
            run_id=state.run_id,
            force=False,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("relationship attach failed")
        deps.tracer.event(
            "orchestrator",
            "relationship_error",
            {"summary": str(exc)},
        )
        return state
    label = assessment.label
    style = BRIEF_STYLES.get(label, "")
    state.context["relationship_label"] = label
    state.context["relationship_confidence"] = assessment.confidence
    state.context["relationship_style"] = style
    state.context["relationship_status"] = assessment.status
    deps.tracer.event(
        "orchestrator",
        "relationship",
        {
            "summary": f"label={label} confidence={assessment.confidence:.2f}",
            "label": label,
            "confidence": assessment.confidence,
            "style": style,
            "status": assessment.status,
        },
    )
    return state


def _maybe_meal_hook(state: RunState, deps: Deps) -> RunState:
    """Offer a one-line eat-ideas caveat when the meeting looks like a meal."""
    utterance = state.utterance.text or ""
    mealish = bool(_MEAL_RE.search(utterance))
    if not mealish:
        for ev in state.evidence.values():
            if ev.source == "calendar" and _MEAL_RE.search(ev.text or ""):
                mealish = True
                break
    if not mealish or not state.entity_id:
        return state
    if not getattr(deps.settings, "preference_profiles", True):
        return state
    cached = deps.world_store.get_preference_profile(state.entity_id)
    if cached is not None and cached.opted_out:
        return state
    # Signal that a profile could be built (do not build here).
    state.context["meal_hook"] = True
    _ = get_preferences  # available for suggest path; briefs stay lazy
    return state


def _clarification_candidates(
    candidates: list[Candidate], *, limit: int = 3
) -> list[Candidate]:
    """Prefer multi-token names when a first-name query hits bare fragments."""
    multi = [c for c in candidates if len(c.name.split()) >= 2]
    single = [c for c in candidates if len(c.name.split()) < 2]
    ordered = multi + single
    # Preserve score order within each bucket.
    ordered.sort(
        key=lambda c: (
            0 if len(c.name.split()) >= 2 else 1,
            -c.score,
            c.name,
        )
    )
    return ordered[:limit]


def _extract_handle(text: str) -> str | None:
    if not text:
        return None
    email = _EMAIL_RE.search(text)
    if email:
        return email.group(0)
    phone = _PHONE_RE.search(text)
    if phone:
        return phone.group(0)
    return None


_PERSON_IN_UTTERANCE = [
    # Keywords case-insensitive; captured proper names stay title-case.
    re.compile(
        r"(?i)\b(?:meeting|meet|calling|call with|call|with|about)\s+"
        r"((?-i:[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+))\b"
    ),
    re.compile(r"(?i)^((?-i:[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+))\s+told\b"),
    re.compile(
        r"(?i)\b(?:note that|remember that|remember)\s+"
        r"((?-i:[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+))\b"
    ),
]


def _extract_person_name_from_text(text: str) -> str | None:
    """Pull a multi-token proper name from common meeting/note phrasings."""
    if not text:
        return None
    for pattern in _PERSON_IN_UTTERANCE:
        match = pattern.search(text.strip())
        if match:
            return match.group(1).strip()
    # Fallback: first multi-token Capitalized span.
    span = re.search(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b", text)
    return span.group(1).strip() if span else None


def _match_clarification_reply(reply: str, candidates: list[Candidate]) -> Candidate | None:
    """Map a user reply to one Candidate: ordinal, full name, or unique last name."""
    text = reply.strip()
    if not text or not candidates:
        return None

    ordinal = _ORDINAL_RE.search(text)
    if ordinal:
        token = ordinal.group(1).lower()
        index_map = {
            "first": 0,
            "1st": 0,
            "1": 0,
            "second": 1,
            "2nd": 1,
            "2": 1,
            "third": 2,
            "3rd": 2,
            "3": 2,
        }
        idx = index_map.get(token)
        if idx is not None and 0 <= idx < len(candidates):
            return candidates[idx]

    if text.isdigit():
        index = int(text) - 1
        if 0 <= index < len(candidates):
            return candidates[index]
        return None

    lowered = text.lower()
    exact = [c for c in candidates if c.name.lower() == lowered]
    if len(exact) == 1:
        return exact[0]

    # Fuzzy / substring on full name or last name.
    scored: list[tuple[float, Candidate]] = []
    for cand in candidates:
        ratio = fuzz.token_set_ratio(text, cand.name) / 100.0
        last = cand.name.lower().rsplit(" ", 1)[-1]
        if lowered == last or lowered in cand.name.lower():
            ratio = max(ratio, 0.9)
        scored.append((ratio, cand))
    scored.sort(key=lambda item: item[0], reverse=True)
    if scored and scored[0][0] >= 0.75:
        if len(scored) == 1 or scored[0][0] - scored[1][0] >= 0.05:
            return scored[0][1]
    return None


async def _retrieve(state: RunState, deps: Deps) -> RunState:
    """Parallel world / hmem / web_scout with timeouts; build evidence pack."""
    entity_id = state.entity_id or ""
    caveats: list[str] = []
    person_ctx: dict[str, Any] = {}
    memories_payload: dict[str, Any] = {"memories": []}
    web_facts: list[dict[str, Any]] = []

    async def _world() -> dict[str, Any]:
        return await asyncio.to_thread(
            deps.world.person_context, entity_id, 1500
        )

    async def _hmem() -> dict[str, Any]:
        return await deps.hmem_retrieval.retrieve(
            state.utterance.text,
            entity_ids=[entity_id] if entity_id else [],
            top_n=5,
        )

    async def _web() -> list[dict[str, Any]]:
        # web_scout not implemented yet
        deps.tracer.event(
            "orchestrator",
            "web_scout_unavailable",
            {"summary": "web_scout_unavailable"},
        )
        return []

    tasks = {
        "world": asyncio.create_task(_world()),
        "hmem": asyncio.create_task(_hmem()),
        "web": asyncio.create_task(_web()),
    }
    results: dict[str, Any] = {}
    for name, task in tasks.items():
        try:
            results[name] = await asyncio.wait_for(task, timeout=RETRIEVE_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001 - continue without source
            logger.exception("retrieve %s failed", name)
            deps.tracer.event(
                "orchestrator",
                "retrieve_error",
                {"summary": f"{name} failed: {exc}", "source": name, "error": str(exc)},
            )
            if name == "hmem":
                caveats.append("Couldn't read memories")
            elif name == "world":
                caveats.append("Couldn't read world context")
            elif name == "web":
                caveats.append("Couldn't read web results")
            results[name] = {} if name != "web" else []

    person_ctx = results.get("world") or {}
    memories_payload = results.get("hmem") or {"memories": []}
    web_facts = results.get("web") or []

    backend = (
        "duckdb"
        if deps.settings.sample_mode or str(deps.settings.data_backend) == "duckdb"
        else "zetaris"
    )
    deps.tracer.event(
        "orchestrator",
        "retrieve",
        {
            "summary": (
                f"world_open_loops={len(person_ctx.get('open_loops') or [])} "
                f"memories={len(memories_payload.get('memories') or [])} "
                f"backend={backend}"
            ),
            "open_loops": len(person_ctx.get("open_loops") or []),
            "recent_interactions": len(person_ctx.get("recent_interactions") or []),
            "past_events": len(person_ctx.get("past_events") or []),
            "upcoming_events": len(person_ctx.get("upcoming_events") or []),
            "conflicts": len(person_ctx.get("conflicts") or []),
            "memories": len(memories_payload.get("memories") or []),
            "web": len(web_facts),
            "backend": backend,
        },
    )
    mem_list = memories_payload.get("memories") or []
    top_score = max((float(m.get("score") or 0) for m in mem_list), default=0.0)
    deps.tracer.event(
        "orchestrator",
        "memories",
        {
            "summary": f"count={len(mem_list)} top_score={top_score:.3f}",
            "count": len(mem_list),
            "top_score": top_score,
        },
    )

    evidence, source_map, tentative = _build_evidence_pack(
        person_ctx,
        memories_payload,
        web_facts,
        tentative_identity=bool(state.context.get("tentative_identity")),
    )
    state.evidence = evidence
    state.context["evidence_source_refs"] = source_map
    state.context["retrieve_caveats"] = caveats
    if tentative:
        state.context["tentative_identity_evidence"] = True
    state.memories = list(mem_list)

    deps.tracer.event(
        "orchestrator",
        "evidence_pack",
        {
            "summary": f"size={len(evidence)} ids={sorted(evidence)}",
            "size": len(evidence),
            "ids": sorted(evidence.keys()),
        },
    )
    return state


def _build_evidence_pack(
    person_ctx: dict[str, Any],
    memories_payload: dict[str, Any],
    web_facts: list[dict[str, Any]],
    *,
    tentative_identity: bool,
) -> tuple[dict[str, Evidence], dict[str, str], bool]:
    """Convert world/hmem/web into Evidence with e*/m*/w* ids."""
    evidence: dict[str, Evidence] = {}
    source_map: dict[str, str] = {}
    now = datetime.now(timezone.utc)
    e_idx = 1
    has_tentative = tentative_identity

    def _ts(value: str | None) -> datetime:
        if not value:
            return now
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return now

    def _add_e(
        *,
        source: str,
        source_ref: str,
        ts: datetime,
        text: str,
        tags: list[str] | None = None,
        url: str | None = None,
    ) -> str:
        nonlocal e_idx
        eid = f"e{e_idx}"
        e_idx += 1
        tag_list = list(tags or [])
        evidence[eid] = Evidence(
            id=eid,
            source=source,  # type: ignore[arg-type]
            source_ref=source_ref,
            ts=ts,
            text=text,
            url=url,
            tags=tag_list,
        )
        source_map[eid] = source_ref
        return eid

    # Priority: open_loops, then interactions, events, conflicts.
    priority: list[tuple[str, dict[str, Any]]] = []
    for item in person_ctx.get("open_loops") or []:
        priority.append(("open_loop", item))
    for item in person_ctx.get("recent_interactions") or []:
        priority.append(("interaction", item))
    for item in person_ctx.get("past_events") or []:
        priority.append(("past_event", item))
    for item in person_ctx.get("upcoming_events") or []:
        priority.append(("upcoming_event", item))
    for item in person_ctx.get("conflicts") or []:
        priority.append(("conflict", item))

    open_loop_and_conflict: list[tuple[str, dict[str, Any]]] = [
        p for p in priority if p[0] in {"open_loop", "conflict"}
    ]
    rest = [p for p in priority if p[0] not in {"open_loop", "conflict"}]

    def _emit(kind: str, item: dict[str, Any]) -> None:
        nonlocal has_tentative
        tags: list[str] = []
        assumption = item.get("assumption")
        if assumption == "assumed" or tentative_identity:
            tags.append("tentative_identity")
            has_tentative = True
        if kind == "open_loop":
            tags.append("open_loop")
            text = item.get("text") or item.get("subject") or "open loop"
            src = item.get("source") or "gmail"
            if src not in {"gmail", "imessage", "whatsapp", "calendar", "web", "memory", "linkedin"}:
                src = "gmail"
            _add_e(
                source=src,
                source_ref=str(item.get("source_ref") or item.get("thread_id") or ""),
                ts=_ts(item.get("ts_utc")),
                text=str(text),
                tags=tags,
            )
        elif kind == "interaction":
            src = item.get("source") or "gmail"
            if src not in {"gmail", "imessage", "whatsapp", "calendar", "web", "memory", "linkedin"}:
                src = "gmail"
            blob = item.get("text") or item.get("subject") or ""
            _add_e(
                source=src,
                source_ref=str(item.get("source_ref") or item.get("interaction_id") or ""),
                ts=_ts(item.get("ts_utc")),
                text=str(blob),
                tags=tags,
            )
        elif kind in {"past_event", "upcoming_event"}:
            title = item.get("title") or "event"
            when = item.get("start_utc") or ""
            loc = item.get("location") or ""
            text = f"{title} at {when}" + (f" ({loc})" if loc else "")
            _add_e(
                source="calendar",
                source_ref=str(item.get("source_ref") or item.get("event_id") or ""),
                ts=_ts(str(when) if when else None),
                text=text,
                tags=tags,
            )
        elif kind == "conflict":
            values = item.get("values") or []
            parts = []
            refs = []
            for val in values:
                if isinstance(val, dict):
                    parts.append(
                        f"{val.get('value')} (from {val.get('source_ref')}, "
                        f"observed {val.get('observed_at')})"
                    )
                    refs.append(str(val.get("source_ref") or ""))
                else:
                    parts.append(str(val))
            field_name = item.get("field") or "attribute"
            text = f"Conflict on {field_name}: " + " vs ".join(parts)
            tags.append("conflict")
            _add_e(
                source="gmail",
                source_ref="|".join(r for r in refs if r) or str(item.get("id") or "conflict"),
                ts=now,
                text=text,
                tags=tags,
            )

    for kind, item in open_loop_and_conflict:
        _emit(kind, item)
    for kind, item in rest:
        if len(evidence) >= EVIDENCE_CAP:
            break
        _emit(kind, item)

    # Memories → m1, m2...
    m_idx = 1
    for mem in memories_payload.get("memories") or []:
        if len(evidence) >= EVIDENCE_CAP and m_idx > 1:
            # Still prefer keeping at least some memories if room; stop at cap.
            if len(evidence) >= EVIDENCE_CAP:
                break
        mid = f"m{m_idx}"
        m_idx += 1
        tags = ["memory"]
        if tentative_identity:
            tags.append("tentative_identity")
            has_tentative = True
        content = str(mem.get("content") or "")
        mem_id = str(mem.get("id") or mid)
        evidence[mid] = Evidence(
            id=mid,
            source="memory",
            source_ref=mem_id,
            ts=now,
            text=content,
            tags=tags,
        )
        source_map[mid] = mem_id
        # Keep mapping from evidence id to original memory id for feedback.
        source_map[f"memory:{mid}"] = mem_id

    # Web → w1, w2...
    w_idx = 1
    for fact in web_facts:
        if len(evidence) >= EVIDENCE_CAP:
            break
        wid = f"w{w_idx}"
        w_idx += 1
        evidence[wid] = Evidence(
            id=wid,
            source="web",
            source_ref=str(fact.get("source_ref") or fact.get("url") or wid),
            ts=_ts(fact.get("ts")),
            text=str(fact.get("text") or ""),
            url=fact.get("url"),
            tags=list(fact.get("tags") or []),
        )
        source_map[wid] = str(fact.get("source_ref") or fact.get("url") or wid)

    # Cap: keep open loops + conflicts, then fill.
    if len(evidence) > EVIDENCE_CAP:
        keep_ids = [
            eid
            for eid, ev in evidence.items()
            if "open_loop" in ev.tags or "conflict" in ev.tags or eid.startswith("m")
        ]
        others = [eid for eid in evidence if eid not in keep_ids]
        ordered = keep_ids + others
        trim = set(ordered[:EVIDENCE_CAP])
        evidence = {k: v for k, v in evidence.items() if k in trim}
        source_map = {k: v for k, v in source_map.items() if k in trim or k.startswith("memory:")}

    return evidence, source_map, has_tentative


async def _handle_note(state: RunState, deps: Deps) -> RunState:
    """Mine a user note into H-MEM and reply with a short confirmation."""
    text = state.utterance.text
    entity_id = state.entity_id or ""
    memories = await deps.hmem_mining.mine(
        MineEvent(
            kind="user_note",
            text=text,
            metadata={"entity_id": entity_id, "run_id": state.run_id},
        )
    )
    # Attach entity id when mining didn't resolve names.
    for mem in memories:
        if entity_id and entity_id not in mem.entity_ids:
            mem.entity_ids = [*mem.entity_ids, entity_id]
            deps.hmem_store.update(mem)

    summary = memories[0].content if memories else text
    person = state.entity_name or "them"
    noted = f"Noted: {summary}" if memories else "Noted."
    state.final_brief = Brief(
        person=person,
        headline=noted,
        points=[],
        caveats=[],
    )
    state.status = "done"
    deps.tracer.event(
        "orchestrator",
        "note",
        {
            "summary": noted,
            "memories": [m.id for m in memories],
            "entity_id": entity_id,
        },
    )
    return state


def _apply_memory_feedback(state: RunState, deps: Deps) -> None:
    """Mark cited memories helpful/wrong from the final critic verdict."""
    if not state.verdicts or not state.final_brief:
        return
    verdict = state.verdicts[-1]
    # Map evidence id → memory store id
    source_map: dict[str, str] = state.context.get("evidence_source_refs") or {}
    kept_points = {
        i
        for i, item in enumerate(verdict.verdicts)
        if item.decision == "keep"
    }
    # Points in final brief after drops — use critic decisions on draft indices.
    draft = state.drafts[-1] if state.drafts else None
    if draft is None:
        return

    feedback_log: list[dict[str, str]] = []
    for item in verdict.verdicts:
        if item.index < 0 or item.index >= len(draft.points):
            continue
        point = draft.points[item.index]
        for eid in point.evidence_ids:
            if not eid.startswith("m"):
                continue
            mem_id = source_map.get(f"memory:{eid}") or source_map.get(eid) or ""
            if not mem_id:
                # Evidence source_ref for m* is the memory id.
                ev = state.evidence.get(eid)
                mem_id = ev.source_ref if ev else ""
            if not mem_id:
                continue
            verdict_label: Literal["helpful", "wrong"] | None = None
            if item.decision == "keep":
                verdict_label = "helpful"
            elif item.decision == "drop":
                verdict_label = "wrong"
            if verdict_label is None:
                continue
            updated = _feedback_memory(deps, mem_id, verdict_label)
            if updated is not None:
                feedback_log.append({"id": mem_id, "verdict": verdict_label})
        _ = kept_points

    if feedback_log:
        deps.tracer.event(
            "orchestrator",
            "feedback",
            {
                "summary": f"feedback={len(feedback_log)}",
                "items": feedback_log,
            },
        )


def _feedback_memory(
    deps: Deps,
    memory_id: str,
    verdict: Literal["helpful", "wrong"],
) -> Any:
    """Apply critic feedback on one memory via the deps store/ledger."""
    mem = deps.hmem_store.get(memory_id)
    if mem is None:
        return None
    before = mem.model_copy(deep=True)
    if verdict == "helpful":
        mem.confidence = min(1.0, mem.confidence + 0.05)
        mem.access_count += 1
        mem.last_accessed = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    else:
        mem.confidence = max(0.0, mem.confidence - 0.2)
        if "review" not in mem.tags:
            mem.tags = [*mem.tags, "review"]
    deps.hmem_store.update(mem)
    if verdict == "helpful" and mem.access_count >= 3:
        promoted = deps.hmem_store.promote(mem.id, reason="access_count")
        if promoted is not None:
            mem = promoted
            deps.hmem_ledger.record("promote", memory_id=mem.id, before=before, after=mem)
    deps.hmem_ledger.record(
        "feedback",
        memory_id=mem.id,
        before=before,
        after=mem,
        extra={"verdict": verdict},
    )
    deps.tracer.event("hmem", "feedback", {"memory_id": memory_id, "verdict": verdict})
    return mem


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
    # Merge retrieve caveats into brief if present on a later call site.
    return draft.model_copy(update={"points": points})


# Re-export for tests that import ResolveCandidate via orchestrator historically.
__all__ = [
    "Deps",
    "LLMCallLimitExceeded",
    "MAX_CRITIC_ROUNDS",
    "MAX_LLM_CALLS",
    "build_deps",
    "default_deps",
    "run",
]
