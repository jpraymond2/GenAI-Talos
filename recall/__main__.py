"""Typer CLI: ask, ingest, serve, and voice."""

import os
from typing import Annotated

import typer

from recall.config import get_settings
from recall.tracing import Tracer

app = typer.Typer(no_args_is_help=True, help="Recall, a voice-first meeting-prep agent.")


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


if __name__ == "__main__":
    app()
