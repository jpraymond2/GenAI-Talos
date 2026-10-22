"""FastAPI app: health check and a live trace socket."""

import asyncio

from fastapi import FastAPI, WebSocket

from recall import __version__
from recall.tracing import Tracer

app = FastAPI(title="Recall", version=__version__)


@app.get("/health")
def health() -> dict[str, str]:
    """Report that the process is up."""
    return {"status": "ok"}


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
