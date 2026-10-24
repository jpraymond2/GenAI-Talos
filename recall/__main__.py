"""Typer CLI: ask, ingest, who, note, memories, serve, voice, llm-test, agent-test."""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Annotated, Literal

import typer
from pydantic import BaseModel, Field

from recall.agents import critic, router, writer
from recall.config import get_settings
from recall.llm.generator import embed, generate, generate_json, resolve_model
from recall.schemas import Brief, Candidate, Evidence, RunState, TalkingPoint, Utterance
from recall.tracing import Tracer

app = typer.Typer(no_args_is_help=True, help="Recall, a voice-first meeting-prep agent.")
identity_app = typer.Typer(no_args_is_help=True, help="Identity assumptions and scoring.")
relationship_app = typer.Typer(no_args_is_help=True, help="Relationship labels.")
preferences_app = typer.Typer(no_args_is_help=True, help="Food/places preference profiles.")
app.add_typer(identity_app, name="identity")
app.add_typer(relationship_app, name="relationship")
app.add_typer(preferences_app, name="preferences")

AgentName = Literal["router", "writer", "critic"]


def _enable_sample_mode() -> None:
    """Force SAMPLE_MODE for this process so the run needs no credentials."""
    os.environ["SAMPLE_MODE"] = "true"
    get_settings.cache_clear()


_WORLD_EMPTY_MSG = "World model is empty. Run: recall ingest --sample (or --live)"


def _require_world_entities() -> None:
    """Exit if the world DB is missing or has zero entities (do not create an empty DB)."""
    from recall.engines.world.store import WorldStore, world_db_path

    settings = get_settings()
    path = world_db_path(settings)
    if not path.is_file():
        print(_WORLD_EMPTY_MSG)
        raise typer.Exit(code=1)
    store = WorldStore(path=path, settings=settings)
    try:
        if store.count_entities() == 0:
            print(_WORLD_EMPTY_MSG)
            raise typer.Exit(code=1)
    finally:
        store.close()


@app.command()
def ingest(
    live: Annotated[
        bool,
        typer.Option("--live", help="Pull Gmail, Calendar, WhatsApp, and iMessage into data/live/."),
    ] = False,
    sample: Annotated[
        bool,
        typer.Option("--sample", help="Build data/world_sample.db from sample CSVs via the query layer."),
    ] = False,
) -> None:
    """Ingest live connectors and/or build the world model (with identity scoring)."""
    if live and sample:
        print("Pass only one of --live or --sample.")
        raise typer.Exit(code=1)
    if not live and not sample:
        print("Ingest needs --live or --sample.")
        raise typer.Exit(code=1)

    if sample:
        _enable_sample_mode()
        settings = get_settings()
        tracer = Tracer(logs_dir=settings.logs_dir)
        with tracer.activate():
            from recall.engines.world import run_world_ingest, world

            world.reset()
            counts = run_world_ingest(sample=True, settings=settings, tracer=tracer)
        print(
            "Sample world ingest complete: "
            f"entities={counts.get('entities')} conflicts={counts.get('conflicts')} "
            f"identity_scored={counts.get('identity_scored')} "
            f"observations={counts.get('observations')} -> data/world_sample.db"
        )
        return

    # Live pulls must not stay in SAMPLE_MODE (DuckDB would ignore parquet).
    os.environ["SAMPLE_MODE"] = "false"
    get_settings.cache_clear()
    settings = get_settings()

    tracer = Tracer(logs_dir=settings.logs_dir)
    with tracer.activate():
        from recall.connectors.ingest import run_live_ingest
        from recall.engines.world import run_world_ingest, world

        summary = run_live_ingest(settings=settings, tracer=tracer)
        world.reset()
        world_counts = run_world_ingest(sample=False, settings=settings, tracer=tracer)

    print("Live ingest complete.")
    for source, info in summary["per_source"].items():
        skipped = info.get("skipped")
        if skipped:
            print(f"  {source}: {skipped}")
        else:
            print(
                f"  {source}: handles={info['handles']} interactions={info['interactions']} "
                f"events={info['events']} contacts={info['contacts']}"
            )
    totals = summary["totals"]
    print(
        "  totals: "
        f"handles={totals['handles']} interactions={totals['interactions']} "
        f"events={totals['events']} contacts={totals['contacts']}"
    )
    print(
        "World ingest complete: "
        f"entities={counts.get('entities')} conflicts={counts.get('conflicts')} "
        f"identity_scored={counts.get('identity_scored')} "
        f"relationships_scored={counts.get('relationships_scored')} -> {db}"
    )


@relationship_app.command("list")
def relationship_list(
    sample: Annotated[bool, typer.Option("--sample", help="Use world_sample.db.")] = True,
) -> None:
    """List latest relationship labels for each person."""
    if sample:
        _enable_sample_mode()
    _require_world_entities()
    from recall.engines.world.store import WorldStore, world_db_path

    store = WorldStore(path=world_db_path())
    rows = store.list_relationship_assessments(latest_only=True)
    if not rows:
        print("No relationship assessments yet. Run: recall ingest --sample")
        return
    for row in sorted(rows, key=lambda r: r.label):
        ent = store.get_entity(row.entity_id)
        name = ent.canonical_name if ent else row.entity_id
        print(
            f"{name}\t{row.label}\tp={row.confidence:.2f}\t"
            f"status={row.status}\t{row.entity_id}"
        )
    store.close()


@relationship_app.command("set")
def relationship_set(
    name: Annotated[str, typer.Argument(help="Person name to resolve.")],
    label: Annotated[
        str,
        typer.Argument(help="social|professional|mixed|family|acquaintance"),
    ],
    sample: Annotated[bool, typer.Option("--sample", help="Use world_sample.db.")] = True,
) -> None:
    """Set a user override relationship label (never replaced by auto)."""
    if sample:
        _enable_sample_mode()
    _require_world_entities()
    from recall.engines.world import world
    from recall.engines.world.relationship import set_relationship_override
    from recall.engines.world.store import WorldStore, world_db_path

    allowed = {"social", "professional", "mixed", "family", "acquaintance"}
    if label not in allowed:
        print(f"label must be one of {sorted(allowed)}")
        raise typer.Exit(code=1)
    world.reset()
    cands = world.resolve(name)
    if not cands:
        print(f"No candidates for {name!r}")
        raise typer.Exit(code=1)
    if len(cands) >= 2 and abs(cands[0].score - cands[1].score) < 0.08:
        print(f"Ambiguous name {name!r}:")
        for c in cands[:5]:
            print(f"  {c.name} ({c.entity_id})")
        raise typer.Exit(code=1)
    chosen = cands[0]
    store = WorldStore(path=world_db_path())
    assessment = set_relationship_override(
        chosen.entity_id, label, store=store  # type: ignore[arg-type]
    )
    print(
        f"override {chosen.name} ({chosen.entity_id}) -> {assessment.label} "
        f"status={assessment.status}"
    )
    store.close()


@preferences_app.command("off")
def preferences_off(
    name: Annotated[str, typer.Argument(help="Person name to opt out.")],
    sample: Annotated[bool, typer.Option("--sample", help="Use world_sample.db.")] = True,
) -> None:
    """Delete preference profile and block rebuilding for this person."""
    if sample:
        _enable_sample_mode()
    _require_world_entities()
    from recall.engines.world import world
    from recall.engines.world.preferences import opt_out_preferences
    from recall.engines.world.store import WorldStore, world_db_path

    world.reset()
    cands = world.resolve(name)
    if not cands:
        print(f"No candidates for {name!r}")
        raise typer.Exit(code=1)
    chosen = cands[0]
    store = WorldStore(path=world_db_path())
    opt_out_preferences(chosen.entity_id, store=store)
    print(f"preferences opted out for {chosen.name} ({chosen.entity_id})")
    store.close()


@preferences_app.command("show")
def preferences_show(
    name: Annotated[str, typer.Argument(help="Person name.")],
    sample: Annotated[bool, typer.Option("--sample", help="Use world_sample.db.")] = True,
) -> None:
    """Build (if needed) and print a food/places preference profile."""
    if sample:
        _enable_sample_mode()
    _require_world_entities()
    from recall.engines.world import world
    from recall.engines.world.preferences import get_preferences
    from recall.engines.world.store import WorldStore, world_db_path
    from recall.data.query_layer import get_query_layer

    world.reset()
    cands = world.resolve(name)
    if not cands:
        print(f"No candidates for {name!r}")
        raise typer.Exit(code=1)
    chosen = cands[0]
    settings = get_settings()
    tracer = Tracer(logs_dir=settings.logs_dir)
    store = WorldStore(path=world_db_path(settings), settings=settings)
    with tracer.activate():
        profile = get_preferences(
            chosen.entity_id,
            store=store,
            settings=settings,
            query_layer=get_query_layer(settings, tracer=tracer),
            tracer=tracer,
            reason="cli_show",
        )
    if profile.opted_out:
        print(f"opted out: {chosen.name}")
        store.close()
        return
    print(f"{chosen.name} ({chosen.entity_id}) items={len(profile.items)} built_at={profile.built_at}")
    for item in profile.items:
        print(
            f"  [{item.sentiment}/{item.kind}] {item.value} "
            f"n={item.mention_count} refs={','.join(item.source_refs[:2])}"
        )
    store.close()


@identity_app.command("list")
def identity_list(
    status: Annotated[
        str | None,
        typer.Option("--status", help="Filter by decision: needs_user|tentative|auto_linked."),
    ] = None,
    sample: Annotated[bool, typer.Option("--sample", help="Use world_sample.db.")] = True,
) -> None:
    """List identity assumptions (latest active rows)."""
    if sample:
        _enable_sample_mode()
    from recall.engines.world.store import WorldStore, world_db_path

    store = WorldStore(path=world_db_path())
    rows = store.list_identity_assumptions(status=status, active_only=False)
    # Prefer one row per handle: latest active/user status
    seen: set[str] = set()
    for row in rows:
        key = str(row["handle_value"]).lower()
        if key in seen:
            continue
        if row["status"] == "superseded":
            continue
        seen.add(key)
        print(
            f"{row['handle_value']}\t{row['decision']}\t"
            f"p={float(row['probability']):.2%}\t"
            f"entity={row['candidate_entity_id']}\tstatus={row['status']}"
        )


@identity_app.command("explain")
def identity_explain(
    handle: Annotated[str, typer.Argument(help="Handle to explain, e.g. name@company.com")],
    sample: Annotated[bool, typer.Option("--sample", help="Use world_sample.db.")] = True,
) -> None:
    """Print the latest score file as a readable table."""
    if sample:
        _enable_sample_mode()
    from recall.engines.world.identity_service import format_explain_table
    from recall.engines.world.store import WorldStore, world_db_path

    store = WorldStore(path=world_db_path())
    rows = store.list_identity_assumptions(handle_value=handle)
    score_file = next((r["score_file"] for r in rows if r.get("score_file")), "")
    if not score_file:
        latest = store.latest_identity_for_handle(handle)
        score_file = (latest or {}).get("score_file") or ""
    if not score_file:
        print(f"No score file for {handle}")
        raise typer.Exit(code=1)
    print(format_explain_table(score_file, handle_value=handle))


@identity_app.command("confirm")
def identity_confirm(
    handle: Annotated[str, typer.Argument()],
    entity_id: Annotated[str, typer.Argument()],
    sample: Annotated[bool, typer.Option("--sample")] = True,
) -> None:
    """Confirm a handle → entity link (permanent)."""
    if sample:
        _enable_sample_mode()
    from recall.engines.world.identity_service import IdentityService

    row = IdentityService().confirm(handle, entity_id)
    print(f"confirmed {handle} -> {entity_id} status={row['status']} p=1.0")


@identity_app.command("reject")
def identity_reject(
    handle: Annotated[str, typer.Argument()],
    entity_id: Annotated[str, typer.Argument()],
    sample: Annotated[bool, typer.Option("--sample")] = True,
) -> None:
    """Reject a handle → entity candidate."""
    if sample:
        _enable_sample_mode()
    from recall.engines.world.identity_service import IdentityService

    row = IdentityService().reject(handle, entity_id)
    print(f"rejected {handle} -/-> {entity_id} status={row['status']}")


@identity_app.command("questions")
def identity_questions(
    sample: Annotated[bool, typer.Option("--sample")] = True,
) -> None:
    """List batched needs_user questions grouped by candidate pair."""
    if sample:
        _enable_sample_mode()
    from recall.engines.world.identity_service import IdentityService

    groups = IdentityService().list_questions()
    if not groups:
        print("No open identity questions.")
        return
    for group in groups:
        print(
            f"pair={group.get('candidate_a')}|{group.get('candidate_b')} "
            f"count={group.get('count')}"
        )
        for handle in group.get("handles") or []:
            print(f"  - {handle}")
        for q in group.get("questions") or []:
            print(f"    {q.get('question')}")


@identity_app.command("bench")
def identity_bench() -> None:
    """Run identity scenario fixtures and print pass/fail table."""
    _enable_sample_mode()
    from recall.engines.world.identity_bench import format_bench_table, run_bench

    payload = run_bench()
    print(format_bench_table(payload))
    if int(payload.get("passed", 0)) < int(payload.get("total", 0)):
        raise typer.Exit(code=1)


@app.command()
def ask(
    utterance: Annotated[str, typer.Argument(help="What you said, in your own words.")],
    sample: Annotated[
        bool,
        typer.Option("--sample", help="Use sample data and do not require credentials."),
    ] = False,
    entity_id: Annotated[
        str | None,
        typer.Option("--entity-id", help="Resolved entity id when disambiguating."),
    ] = None,
    run_id: Annotated[
        str | None,
        typer.Option("--run-id", help="Resume a prior needs_clarification run."),
    ] = None,
) -> None:
    """Run the meeting-prep orchestrator and print the brief."""
    if sample:
        _enable_sample_mode()
    asyncio.run(_run_ask(utterance, entity_id=entity_id, run_id=run_id))


@app.command()
def note(
    text: Annotated[str, typer.Argument(help="What to remember.")],
    sample: Annotated[
        bool,
        typer.Option("--sample", help="Use sample data and do not require credentials."),
    ] = False,
) -> None:
    """Store a note in H-MEM (resolves the person first)."""
    if sample:
        _enable_sample_mode()
    _require_world_entities()
    asyncio.run(_run_note(text))


async def _run_note(text: str) -> None:
    from recall.orchestrator import build_deps, run as orchestrate
    from recall.schemas import Intent

    deps = build_deps(sample_mode=True if get_settings().sample_mode else None)
    state = await orchestrate(text, deps=deps, force_kind="note")
    if state.status == "needs_clarification" and state.candidates:
        chosen = _prompt_for_candidate(state)
        if chosen is None:
            _print_ask_result(state)
            return
        print(f"resolved: {chosen[1]} ({chosen[0]})")
        state = await orchestrate(
            text,
            run_id=state.run_id,
            clarification_reply=chosen[1],
            deps=deps,
        )
    _print_ask_result(state)
    _ = Intent  # kept for type clarity in tests importing this module


async def _run_ask(
    utterance: str,
    *,
    entity_id: str | None,
    run_id: str | None = None,
) -> None:
    from recall.orchestrator import run as orchestrate

    state = await orchestrate(utterance, entity_id=entity_id, run_id=run_id)
    if state.status == "needs_clarification" and state.candidates:
        chosen = _prompt_for_candidate(state)
        if chosen is None:
            _print_ask_result(state)
            return
        print(f"resolved: {chosen[1]} ({chosen[0]})")
        # Continue the same run with the clarification reply.
        state = await orchestrate(
            utterance,
            run_id=state.run_id,
            clarification_reply=chosen[1],
        )
    _print_ask_result(state)


def _prompt_for_candidate(state: RunState) -> tuple[str, str] | None:
    """Ask which candidate, accepting 1/2, full name, or last name."""
    _print_clarification_prompt(state)
    try:
        reply = input("Which one? (number, full name, or last name): ")
    except EOFError:
        print()
        return None
    if not reply.strip():
        return None
    matched = match_clarification_reply(reply, state.candidates)
    if matched is None:
        print(f"Could not match {reply!r} to a candidate.")
        return None
    return matched.entity_id, matched.name


def _print_clarification_prompt(state: RunState) -> None:
    print("status: needs_clarification")
    print(f"question: {state.question}")
    print("candidates:")
    for index, candidate in enumerate(state.candidates, start=1):
        print(f"{index}. {candidate.name} ({candidate.entity_id}) — {candidate.reason}")


def match_clarification_reply(
    reply: str, candidates: list[Candidate]
) -> Candidate | None:
    """Map a user reply to one Candidate: index, full name, or unique last name."""
    text = reply.strip()
    if not text or not candidates:
        return None

    if text.isdigit():
        index = int(text) - 1
        if 0 <= index < len(candidates):
            return candidates[index]
        return None

    lowered = text.lower()

    exact = [c for c in candidates if c.name.lower() == lowered]
    if len(exact) == 1:
        return exact[0]

    last_name = [c for c in candidates if c.name.lower().rsplit(" ", 1)[-1] == lowered]
    if len(last_name) == 1:
        return last_name[0]

    contains = [c for c in candidates if lowered in c.name.lower()]
    if len(contains) == 1:
        return contains[0]

    return None


def _print_ask_result(state: RunState) -> None:
    latency_ms = state.context.get("latency_ms", "?")
    rounds = state.context.get("critic_rounds", state.round)

    if state.status == "needs_clarification":
        _print_clarification_prompt(state)
        print(f"critic_rounds: {rounds}")
        print(f"latency_ms: {latency_ms}")
        return

    brief = state.final_brief
    if brief is None:
        print(f"status: {state.status}")
        print(f"latency_ms: {latency_ms}")
        return

    print(f"headline: {brief.headline}")
    for index, point in enumerate(brief.points, start=1):
        sources = _sources_for_point(state, point.evidence_ids)
        ids = ", ".join(point.evidence_ids)
        source_note = f" ({sources})" if sources else ""
        print(f"{index}. {point.text} [{ids}]{source_note}")
    if brief.caveats:
        print("caveats:")
        for caveat in brief.caveats:
            print(f"- {caveat}")
    print(f"critic_rounds: {rounds}")
    print(f"latency_ms: {latency_ms}")


def _sources_for_point(state: RunState, evidence_ids: list[str]) -> str:
    labels: list[str] = []
    for evidence_id in evidence_ids:
        item = state.evidence.get(evidence_id)
        if item is not None:
            labels.append(item.source)
    return ", ".join(labels)


@app.command()
def memories(
    name: Annotated[str, typer.Argument(help="Person name to resolve, then list memories.")],
    sample: Annotated[
        bool,
        typer.Option("--sample", help="Use SAMPLE_MODE world + hmem sample DBs."),
    ] = False,
) -> None:
    """Resolve a name via the world model and print ranked memories with why."""
    if sample:
        _enable_sample_mode()
    _require_world_entities()
    settings = get_settings()
    tracer = Tracer(logs_dir=settings.logs_dir)
    with tracer.activate():
        from recall.engines import hmem as hmem_mod
        from recall.engines.world import world

        world.reset()
        hmem_mod.hmem.reset()
        candidates = world.resolve(name)
        if not candidates:
            print(f"No candidates for {name!r}.")
            return
        top = candidates[:2]
        exact = [c for c in candidates if c.name.lower() == name.strip().lower()]
        if not exact and len(top) >= 2 and abs(top[0].score - top[1].score) < 0.08:
            print(f"Ambiguous name {name!r}. Candidates:")
            for cand in candidates[:5]:
                print(f"  {cand.name} ({cand.entity_id}) score={cand.score:.2f}")
            return
        chosen = exact[0] if exact else candidates[0]
        result = hmem_mod.hmem.retrieve(
            f"meeting {chosen.name}",
            [chosen.entity_id],
            top_n=10,
        )
    print(f"Memories for {chosen.name} ({chosen.entity_id}):")
    hits = result.get("memories") or []
    if not hits:
        print("  (none)")
        return
    for hit in hits:
        print(f"  [{hit.get('score', 0):.2f}] {hit.get('content')}")
        if hit.get("why"):
            print(f"      why: {hit.get('why')}")


@app.command()
def serve(
    host: Annotated[str, typer.Option(help="Interface to bind.")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="Port to bind.")] = 8000,
) -> None:
    """Run the FastAPI server."""
    import uvicorn

    uvicorn.run("recall.server.app:app", host=host, port=port)


@app.command()
def voice(
    check: Annotated[
        bool,
        typer.Option(
            "--check",
            help="Validate NVIDIA_API_KEY + Riva ASR (no microphone).",
        ),
    ] = False,
) -> None:
    """Push-to-talk: hold right Ctrl (Windows) / right Option (macOS), then release."""
    if check:
        from recall.voice.check import print_voice_checks

        raise typer.Exit(code=print_voice_checks())

    from recall.voice.loop import run_voice_loop

    run_voice_loop()


class _TinySchema(BaseModel):
    name: str
    count: int = Field(ge=0)


@app.command("llm-test")
def llm_test() -> None:
    """Smoke-test generate, generate_json, and embed against the configured provider."""
    asyncio.run(_run_llm_test())


async def _run_llm_test() -> None:
    settings = get_settings()
    tracer = Tracer(logs_dir=settings.logs_dir)
    with tracer.activate():
        await _print_generate("writer", "Say hello in five words.")
        await _print_generate("critic", "Give one short critique sentence.")
        await _print_generate_json()
        await _print_embed()


async def _print_generate(role: str, prompt: str) -> None:
    model = resolve_model(role)
    started = time.perf_counter()
    text = await generate(prompt, role=role)
    latency_ms = int((time.perf_counter() - started) * 1000)
    preview = text.replace("\n", " ")[:120]
    print(f"[generate role={role}] model={model} latency_ms={latency_ms} preview={preview!r}")


async def _print_generate_json() -> None:
    role = "actions"
    model = resolve_model(role)
    started = time.perf_counter()
    result = await generate_json(
        "Return a person named Ada with count 3.",
        "You invent tiny structured facts.",
        _TinySchema,
        role=role,
    )
    latency_ms = int((time.perf_counter() - started) * 1000)
    print(
        f"[generate_json role={role}] model={model} latency_ms={latency_ms} "
        f"preview={result.model_dump()!r}"
    )


async def _print_embed() -> None:
    settings = get_settings()
    embed_model = (
        "sample"
        if settings.sample_mode or not settings.nvidia_api_key.get_secret_value()
        else settings.nvidia_embed_model
    )
    started = time.perf_counter()
    vectors = await embed(
        ["Ada works on memory systems.", "The meeting is about launch prep."],
        input_type="passage",
    )
    latency_ms = int((time.perf_counter() - started) * 1000)
    preview = [round(v, 4) for v in vectors[0][:6]] if vectors else []
    print(
        f"[embed] model={embed_model} latency_ms={latency_ms} "
        f"count={len(vectors)} dim={len(vectors[0]) if vectors else 0} preview={preview!r}"
    )


@app.command("agent-test")
def agent_test(
    agent: Annotated[
        AgentName,
        typer.Argument(help="Agent to run: router, writer, or critic."),
    ],
    text: Annotated[str, typer.Argument(help="Utterance text to feed the agent.")],
) -> None:
    """Run one agent against sample or live LLM settings and print its output."""
    asyncio.run(_run_agent_test(agent, text))


async def _run_agent_test(agent: AgentName, text: str) -> None:
    settings = get_settings()
    tracer = Tracer(logs_dir=settings.logs_dir)
    deps = SimpleNamespace(tracer=tracer)
    with tracer.activate():
        if agent == "router":
            await _agent_test_router(text, deps)
        elif agent == "writer":
            await _agent_test_writer(text, deps)
        else:
            await _agent_test_critic(text, deps)


def _new_state(text: str) -> RunState:
    return RunState(
        run_id=str(uuid.uuid4()),
        utterance=Utterance(text=text, ts=datetime.now(timezone.utc), mode="text"),
    )


def _demo_state(text: str) -> RunState:
    """Minimal state for agent-test without fixture packs."""
    from datetime import datetime as dt

    state = _new_state(text)
    state.entity_name = "Ada Lovelace"
    state.evidence = {
        "e1": Evidence(
            id="e1",
            source="gmail",
            source_ref="demo:e1",
            ts=dt.now(timezone.utc),
            text="Can you intro me to a design partner next week?",
            tags=["open_loop"],
        ),
        "e2": Evidence(
            id="e2",
            source="whatsapp",
            source_ref="demo:e2",
            ts=dt.now(timezone.utc),
            text="Moving offices in January — exciting times.",
            tags=[],
        ),
    }
    return state


async def _agent_test_router(text: str, deps: SimpleNamespace) -> None:
    model = resolve_model("router")
    state = _new_state(text)
    started = time.perf_counter()
    state = await router.run(state, deps)
    latency_ms = int((time.perf_counter() - started) * 1000)
    assert state.intent is not None
    print(json.dumps(state.intent.model_dump(mode="json"), indent=2))
    print(f"model={model} latency_ms={latency_ms}")


async def _agent_test_writer(text: str, deps: SimpleNamespace) -> None:
    model = resolve_model("writer")
    state = _demo_state(text)
    started = time.perf_counter()
    state = await writer.run(state, deps)
    latency_ms = int((time.perf_counter() - started) * 1000)
    brief = state.drafts[-1]
    print(f"headline: {brief.headline}")
    for index, point in enumerate(brief.points, start=1):
        ids = ", ".join(point.evidence_ids)
        print(f"{index}. {point.text} [{ids}]")
    if brief.caveats:
        print("caveats:")
        for caveat in brief.caveats:
            print(f"- {caveat}")
    print(f"model={model} latency_ms={latency_ms}")


async def _agent_test_critic(text: str, deps: SimpleNamespace) -> None:
    writer_model = resolve_model("writer")
    critic_model = resolve_model("critic")
    state = _demo_state(text)

    started = time.perf_counter()
    state = await writer.run(state, deps)
    writer_ms = int((time.perf_counter() - started) * 1000)

    started = time.perf_counter()
    state = await critic.run(state, deps)
    critic_ms = int((time.perf_counter() - started) * 1000)

    verdict = state.verdicts[-1]
    print(f"overall: {verdict.overall}")
    for item in verdict.verdicts:
        print(f"point {item.index}: {item.decision} — {item.reason}")
        if item.fix_hint:
            print(f"  fix_hint: {item.fix_hint}")
    if verdict.missing:
        print("missing:")
        for note_line in verdict.missing:
            print(f"- {note_line}")
    if verdict.refetch:
        print("refetch:")
        for source in verdict.refetch:
            print(f"- {source}")
    print(
        f"writer_model={writer_model} writer_latency_ms={writer_ms} "
        f"critic_model={critic_model} critic_latency_ms={critic_ms}"
    )
    _ = Brief, TalkingPoint


if __name__ == "__main__":
    app()
