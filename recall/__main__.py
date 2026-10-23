"""Typer CLI: ask, ingest, serve, voice, and llm-test."""

from __future__ import annotations

import asyncio
import os
import time
from typing import Annotated

import typer
from pydantic import BaseModel, Field

from recall.config import get_settings
from recall.llm.generator import embed, generate, generate_json, resolve_model
from recall.tracing import Tracer

app = typer.Typer(no_args_is_help=True, help="Recall, a voice-first meeting-prep agent.")


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
    """Ingest live connectors and/or build the world model SQLite store."""
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
            f"entities={counts['entities']} conflicts={counts['conflicts']} "
            f"observations={counts['observations']} -> data/world_sample.db"
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
        "  world: "
        f"entities={world_counts['entities']} conflicts={world_counts['conflicts']} "
        f"-> data/world.db"
    )


@app.command()
def who(
    name: Annotated[str, typer.Argument(help="Person name to resolve.")],
    sample: Annotated[
        bool,
        typer.Option("--sample", help="Read data/world_sample.db (SAMPLE_MODE)."),
    ] = False,
) -> None:
    """Print ranked world-model candidates with attributes and conflicts."""
    if sample:
        _enable_sample_mode()
    settings = get_settings()
    tracer = Tracer(logs_dir=settings.logs_dir)
    with tracer.activate():
        from recall.engines.world import world
        from recall.engines.world.store import WorldStore, world_db_path

        world.reset()
        db = world_db_path(settings)
        if not db.is_file():
            print(f"No world DB at {db}. Run: recall ingest --sample")
            raise typer.Exit(code=1)
        candidates = world.resolve(name)
        store = WorldStore(path=db, settings=settings)
        if not candidates:
            print(f"No candidates for {name!r}.")
            return
        print(f"Candidates for {name!r}:")
        for cand in candidates:
            entity = store.get_entity(cand.entity_id)
            print(f"  [{cand.score:.2f}] {cand.name} ({cand.entity_id}) — {cand.reason}")
            if entity is None:
                continue
            # Merged channels inferred from aliases / attribute history source_refs.
            channel_hints: set[str] = set()
            for alias in entity.aliases:
                if "@s.whatsapp.net" in alias:
                    channel_hints.add("whatsapp")
                elif "@" in alias:
                    channel_hints.add("gmail")
                elif alias.startswith("+"):
                    channel_hints.add("imessage/phone")
            for hist in entity.attribute_history:
                prefix = hist.source_ref.split(":", 1)[0]
                if prefix in {"gmail", "whatsapp", "imessage", "calendar", "contact"}:
                    channel_hints.add(prefix)
            if channel_hints:
                print(f"      sources: {', '.join(sorted(channel_hints))}")
            if entity.attributes:
                attrs = ", ".join(f"{k}={v}" for k, v in entity.attributes.items())
                print(f"      attributes: {attrs}")
            conflicts = store.list_conflicts(entity.id)
            for conflict in conflicts:
                vals = "; ".join(
                    f"{item.value} ({item.source_ref})" for item in conflict.values
                )
                print(f"      conflict[{conflict.field}]: {vals}")
        store.close()


@app.command()
def note(
    text: Annotated[str, typer.Argument(help="Note text to mine into H-MEM.")],
    sample: Annotated[
        bool,
        typer.Option("--sample", help="Use SAMPLE_MODE (hmem_sample.db + offline LLM)."),
    ] = False,
) -> None:
    """Mine a user note into hierarchical memory and print created memories."""
    if sample:
        _enable_sample_mode()
    _require_world_entities()
    settings = get_settings()
    tracer = Tracer(logs_dir=settings.logs_dir)
    with tracer.activate():
        from recall.engines import hmem as hmem_mod

        hmem_mod.hmem.reset()
        created = hmem_mod.hmem.mine({"kind": "user_note", "text": text})
    if not created:
        print("No memories created.")
        return
    print(f"Created {len(created)} memor{'y' if len(created) == 1 else 'ies'}:")
    for mem in created:
        print(f"  [{mem.layer}/{mem.type}] {mem.id}: {mem.content}")
        if mem.entity_ids:
            print(f"      entities: {', '.join(mem.entity_ids)}")


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
        # Ambiguous if top two scores are close and query is not an exact full-name hit.
        top = candidates[:2]
        exact = [c for c in candidates if c.name.lower() == name.strip().lower()]
        if not exact and len(top) >= 2 and abs(top[0].score - top[1].score) < 0.08:
            print(f"Ambiguous name {name!r}. Candidates:")
            for cand in candidates:
                print(f"  [{cand.score:.2f}] {cand.name} ({cand.entity_id}) — {cand.reason}")
            return
        chosen = exact[0] if exact else candidates[0]
        result = hmem_mod.hmem.retrieve(f"meeting {chosen.name}", [chosen.entity_id])
        print(f"Memories for {chosen.name} ({chosen.entity_id}):")
        hits = result.get("memories") or []
        if not hits:
            print("  (none)")
            return
        for hit in hits:
            print(f"  [{hit['score']:.2f}] {hit['content']}")
            print(f"      why: {hit['why']}")


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
        ["Karim works on memory systems.", "The meeting is about launch prep."],
        input_type="passage",
    )
    latency_ms = int((time.perf_counter() - started) * 1000)
    preview = [round(v, 4) for v in vectors[0][:6]] if vectors else []
    print(
        f"[embed] model={embed_model} latency_ms={latency_ms} "
        f"count={len(vectors)} dim={len(vectors[0]) if vectors else 0} preview={preview!r}"
    )


if __name__ == "__main__":
    app()
