"""FastAPI app: health check, voice upload, live trace socket, optional web/dist."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from recall import __version__
from recall.config import get_settings
from recall.tracing import Tracer

app = FastAPI(title="Recall", version=__version__)

_WEB_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"
_WEB_INDEX = _WEB_DIST / "index.html"


@app.get("/health")
def health() -> dict[str, str]:
    """Report that the process is up."""
    return {"status": "ok"}


@app.post("/api/voice")
async def api_voice(
    audio: UploadFile = File(..., description="WAV, or webm/mp4 if ffmpeg is available"),
) -> dict[str, Any]:
    """Transcribe an uploaded utterance and optionally run respond()."""
    from recall.voice.audio_convert import UnsupportedAudio, ensure_wav_16k_mono
    from recall.voice.loop import respond
    from recall.voice.stt import TranscriptionError, transcribe_detailed

    settings = get_settings()
    tracer = Tracer(logs_dir=settings.logs_dir)
    raw = await audio.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Empty audio upload")

    try:
        wav = ensure_wav_16k_mono(
            raw,
            content_type=audio.content_type or "",
            filename=audio.filename or "",
        )
    except UnsupportedAudio as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc

    with tracer.activate():
        try:
            transcript, engine = transcribe_detailed(
                wav, settings=settings, tracer=tracer
            )
        except TranscriptionError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        result = respond(transcript)
        payload: dict[str, Any] = {
            "transcript": transcript,
            "engine": engine,
            "speech": result.get("speech"),
            "status": result.get("status"),
        }
        if "headline" in result:
            payload["headline"] = result["headline"]
        if "points" in result:
            payload["points"] = result["points"]
        if "question" in result:
            payload["question"] = result["question"]
        return payload


@app.websocket("/ws/trace")
async def trace_events(websocket: WebSocket) -> None:
    """Push each trace event as a JSON text frame.

    A disconnect ends the loop so the subscriber queue does not leak.
    """
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
        """SPA fallback: known files from dist, otherwise index.html.

        Skips API/WS paths so they are not shadowed.
        """
        if spa_path.startswith(("api/", "ws/", "health")):
            # Let FastAPI return 404 for unknown API routes.
            from fastapi import HTTPException

            raise HTTPException(status_code=404)
        candidate = _WEB_DIST / spa_path
        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_WEB_INDEX)
