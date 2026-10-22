"""Typer CLI: ask, ingest, serve, voice, llm-test, and agent-test."""

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
from recall.fixtures import load_fixture_evidence
from recall.llm.generator import embed, generate, generate_json, resolve_model
from recall.schemas import RunState, Utterance
from recall.tracing import Tracer

app = typer.Typer(no_args_is_help=True, help="Recall, a voice-first meeting-prep agent.")

AgentName = Literal["router", "writer", "critic"]


def _enable_sample_mode() -> None:
    """Force SAMPLE_MODE for this process so the run needs no credentials."""
    os.environ["SAMPLE_MODE"] = "true"
    get_settings.cache_clear()


@app.command()
def ask(
    utterance: Annotated[str, typer.Argument(help="What you said, in your own words.")],
    sample: Annotated[
        bool,
        typer.Option("--sample", help="Use sample data and do not require credentials."),
    ] = False,
) -> None:
    """Log one trace event and confirm the skeleton runs."""
    if sample:
        _enable_sample_mode()
    settings = get_settings()
    tracer = Tracer(logs_dir=settings.logs_dir)
    tracer.event(
        "cli",
        "ask",
        {"utterance": utterance, "sample_mode": settings.sample_mode},
    )
    print("Recall skeleton OK")


@app.command()
def ingest() -> None:
    """Load source data into the world store."""
    print("ingest is not implemented yet")


@app.command()
def serve(
    host: Annotated[str, typer.Option(help="Interface to bind.")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="Port to bind.")] = 8000,
) -> None:
    """Run the FastAPI server."""
    import uvicorn

    uvicorn.run("recall.server.app:app", host=host, port=port)


@app.command()
def voice() -> None:
    """Listen and answer by voice."""
    print("voice is not implemented yet")


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
        ["Karim works on memory systems.", "The meeting is about launch prep."],
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


def _karim_state(text: str) -> RunState:
    state = _new_state(text)
    state.entity_name = "Karim Haddad"
    state.evidence = load_fixture_evidence("Karim Haddad")
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
    state = _karim_state(text)
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
    state = _karim_state(text)

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
        for note in verdict.missing:
            print(f"- {note}")
    if verdict.refetch:
        print("refetch:")
        for source in verdict.refetch:
            print(f"- {source}")
    print(
        f"writer_model={writer_model} writer_latency_ms={writer_ms} "
        f"critic_model={critic_model} critic_latency_ms={critic_ms}"
    )


if __name__ == "__main__":
    app()
