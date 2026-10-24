"""FastAPI app: authenticated ask/voice/status, trace socket, phone + desktop web."""

from __future__ import annotations

import asyncio
import csv
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile, WebSocket
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from recall import __version__
from recall.config import get_settings
from recall.server.auth import require_bearer, require_ws_token
from recall.server.session import (
    enrich_ask_payload,
    ingest_status,
    run_ask,
)
from recall.tracing import Tracer

logger = logging.getLogger(__name__)

_WEB_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"
_WEB_INDEX = _WEB_DIST / "index.html"
_INGEST_INTERVAL_S = 15 * 60
_ingest_lock = asyncio.Lock()
_ingest_task: asyncio.Task[None] | None = None


class AskBody(BaseModel):
    """Dashboard / phone ask payload."""

    text: str = Field(min_length=1)
    run_id: str | None = None
    entity_id: str | None = None


class ConfirmBody(BaseModel):
    """Dashboard action-confirmation payload."""

    run_id: str = Field(min_length=1)
    confirmed: bool


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Start the live-ingest loop when not in SAMPLE_MODE."""
    global _ingest_task
    settings = get_settings()
    if not settings.sample_mode:
        _ingest_task = asyncio.create_task(_live_ingest_loop(), name="recall-live-ingest")
    try:
        yield
    finally:
        if _ingest_task is not None:
            _ingest_task.cancel()
            try:
                await _ingest_task
            except asyncio.CancelledError:
                pass
            _ingest_task = None


app = FastAPI(title="Recall", version=__version__, lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, str]:
    """Report that the process is up (no auth)."""
    return {"status": "ok"}


@app.post("/api/ask", dependencies=[Depends(require_bearer)])
async def api_ask(body: AskBody) -> dict[str, Any]:
    """Run or continue meeting-prep; traces fan out on ``/ws/trace``."""
    state = await run_ask(body.text, run_id=body.run_id, entity_id=body.entity_id)
    return enrich_ask_payload(state)


@app.post("/api/confirm", dependencies=[Depends(require_bearer)])
async def api_confirm(body: ConfirmBody) -> dict[str, Any]:
    """Confirm or cancel a proposed action plan when actions supports it."""
    from recall.agents import actions

    confirm_fn = getattr(actions, "confirm", None)
    execute_fn = getattr(actions, "execute", None)
    fn = confirm_fn if callable(confirm_fn) else execute_fn if callable(execute_fn) else None
    if fn is None:
        return {"status": "not_implemented", "run_id": body.run_id}

    result = fn(run_id=body.run_id, confirmed=body.confirmed)
    if asyncio.iscoroutine(result):
        result = await result
    if isinstance(result, dict):
        return result
    if hasattr(result, "model_dump"):
        return result.model_dump(mode="json")
    return {"status": "ok", "run_id": body.run_id, "result": result}


@app.get("/api/person/{entity_id}", dependencies=[Depends(require_bearer)])
def api_person(entity_id: str) -> dict[str, Any]:
    """Return world-model person context for the dashboard Person tab."""
    from recall.engines.world import world

    world.reset()
    ctx = world.person_context(entity_id)
    if not ctx:
        raise HTTPException(status_code=404, detail=f"Unknown entity_id {entity_id!r}")
    return ctx


@app.get("/api/memories/{entity_id}", dependencies=[Depends(require_bearer)])
def api_memories(entity_id: str) -> dict[str, Any]:
    """Return H-MEM hits for the dashboard Memory tab."""
    from recall.engines import hmem as hmem_mod

    hmem_mod.hmem.reset()
    return hmem_mod.retrieve("meeting", [entity_id])


@app.get("/api/status", dependencies=[Depends(require_bearer)])
def api_status() -> dict[str, Any]:
    """Last ingest time plus row counts per source table."""
    counts = _source_row_counts()
    return {
        "last_ingest_at": ingest_status.last_ingest_at,
        "per_source": ingest_status.per_source or counts,
        "totals": ingest_status.totals or counts,
        "sample_mode": get_settings().sample_mode,
    }


@app.post("/api/voice", dependencies=[Depends(require_bearer)])
async def api_voice(
    audio: UploadFile = File(..., description="WAV, webm, or iOS AAC mp4"),
    run_id: str | None = Form(default=None),
) -> dict[str, Any]:
    """Transcribe upload then run the same flow as ``/api/ask``."""
    import time

    from recall.voice.audio_convert import UnsupportedAudio, ensure_wav_16k_mono
    from recall.voice.stt import TranscriptionError, transcribe_detailed

    settings = get_settings()
    tracer = Tracer(logs_dir=settings.logs_dir)
    content_type = audio.content_type or ""
    t0 = time.perf_counter()
    raw = await audio.read()
    upload_ms = (time.perf_counter() - t0) * 1000.0
    size = len(raw)
    logger.info(
        "voice upload: content_type=%s size=%d filename=%s",
        content_type,
        size,
        audio.filename or "",
    )
    print(
        f"[voice] upload content_type={content_type!r} size={size} "
        f"filename={audio.filename!r}",
        flush=True,
    )
    if not raw:
        logger.warning("voice 400: empty upload content_type=%s size=0", content_type)
        print("[voice] 400 empty upload", flush=True)
        raise HTTPException(status_code=400, detail="Empty audio upload")

    t1 = time.perf_counter()
    try:
        wav = ensure_wav_16k_mono(
            raw,
            content_type=content_type,
            filename=audio.filename or "",
        )
    except UnsupportedAudio as exc:
        detail = str(exc)
        logger.warning(
            "voice 415: content_type=%s size=%d detail=%s",
            content_type,
            size,
            detail,
        )
        print(
            f"[voice] 415 content_type={content_type!r} size={size} detail={detail}",
            flush=True,
        )
        raise HTTPException(status_code=415, detail=detail) from exc
    ffmpeg_ms = (time.perf_counter() - t1) * 1000.0

    with tracer.activate():
        t2 = time.perf_counter()
        try:
            transcript, engine = transcribe_detailed(
                wav, settings=settings, tracer=tracer
            )
        except TranscriptionError as exc:
            detail = str(exc)
            logger.warning(
                "voice 503: content_type=%s size=%d detail=%s",
                content_type,
                size,
                detail,
            )
            print(
                f"[voice] 503 content_type={content_type!r} size={size} detail={detail}",
                flush=True,
            )
            raise HTTPException(status_code=503, detail=detail) from exc
        stt_ms = (time.perf_counter() - t2) * 1000.0

        t3 = time.perf_counter()
        state = await run_ask(transcript, run_id=run_id)
        ask_ms = (time.perf_counter() - t3) * 1000.0

        timing = {
            "upload_ms": round(upload_ms, 1),
            "ffmpeg_ms": round(ffmpeg_ms, 1),
            "stt_ms": round(stt_ms, 1),
            "ask_ms": round(ask_ms, 1),
            "total_ms": round((time.perf_counter() - t0) * 1000.0, 1),
            "bytes": size,
            "content_type": content_type,
            "engine": engine,
        }
        tracer.event("voice", "voice_timing", timing)
        print(f"[voice] voice_timing {timing}", flush=True)

    payload = enrich_ask_payload(state)
    payload["transcript"] = transcript
    payload["engine"] = engine
    payload["voice_timing"] = timing
    return payload


@app.websocket("/ws/trace")
async def trace_events(websocket: WebSocket) -> None:
    """Push each trace event as a JSON text frame (``?token=`` required)."""
    if not await require_ws_token(websocket):
        return
    await websocket.accept()
    queue = Tracer.subscribe()
    try:
        while True:
            incoming = asyncio.create_task(queue.get())
            received = asyncio.create_task(websocket.receive())
            done, pending = await asyncio.wait(
                {incoming, received},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            if received in done:
                message = received.result()
                if message.get("type") == "websocket.disconnect":
                    break
                continue
            await websocket.send_json(incoming.result())
    finally:
        Tracer.unsubscribe(queue)


async def _live_ingest_loop() -> None:
    """Every 15 minutes: live connectors under an asyncio lock (no overlap)."""
    while True:
        try:
            await _run_live_ingest_once()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("background live ingest failed")
        await asyncio.sleep(_INGEST_INTERVAL_S)


async def _run_live_ingest_once() -> None:
    if _ingest_lock.locked():
        return
    async with _ingest_lock:
        settings = get_settings()
        tracer = Tracer(logs_dir=settings.logs_dir)
        with tracer.activate():
            tracer.event("ingest", "start", {"mode": "live", "summary": "background"})
            started = datetime.now(timezone.utc).isoformat()

            def _work() -> dict[str, Any]:
                from recall.connectors.ingest import run_live_ingest
                from recall.engines.world import run_world_ingest, world

                summary = run_live_ingest(settings=settings, tracer=tracer)
                world.reset()
                world_counts = run_world_ingest(
                    sample=False, settings=settings, tracer=tracer
                )
                return {"summary": summary, "world": world_counts}

            result = await asyncio.to_thread(_work)
            summary = result["summary"]
            ingest_status.last_ingest_at = started
            ingest_status.per_source = summary.get("per_source") or {}
            ingest_status.totals = summary.get("totals") or {}
            tracer.event(
                "ingest",
                "end",
                {
                    "summary": "background complete",
                    "totals": ingest_status.totals,
                    "world": result.get("world"),
                },
            )


def _source_row_counts() -> dict[str, int]:
    """Count handles/interactions/events/contacts from sample CSV or live parquet."""
    settings = get_settings()
    tables = ("handles", "interactions", "events", "contacts")
    if settings.sample_mode:
        root = Path(settings.sample_data_dir)
        out: dict[str, int] = {}
        for name in tables:
            path = root / f"{name}.csv"
            if not path.is_file():
                out[name] = 0
                continue
            with path.open(encoding="utf-8", newline="") as fh:
                rows = list(csv.reader(fh))
            out[name] = max(0, len(rows) - 1) if rows else 0
        return out

    root = Path(settings.live_data_dir)
    out = {}
    for name in tables:
        path = root / f"{name}.parquet"
        if not path.is_file():
            out[name] = 0
            continue
        try:
            import duckdb

            n = duckdb.sql(f"SELECT COUNT(*) FROM read_parquet('{path.as_posix()}')").fetchone()
            out[name] = int(n[0]) if n else 0
        except Exception:  # noqa: BLE001
            out[name] = 0
    return out


if _WEB_DIST.is_dir() and _WEB_INDEX.is_file():
    app.mount(
        "/assets",
        StaticFiles(directory=_WEB_DIST / "assets"),
        name="web_assets",
    )

    @app.get("/")
    async def spa_index() -> FileResponse:
        """Serve the built dashboard when web/dist exists."""
        return FileResponse(_WEB_INDEX)

    @app.get("/{spa_path:path}")
    async def spa_fallback(spa_path: str) -> FileResponse:
        """SPA fallback: known files from dist, otherwise index.html."""
        if spa_path.startswith(("api/", "ws/", "health")):
            raise HTTPException(status_code=404)
        candidate = _WEB_DIST / spa_path
        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_WEB_INDEX)
