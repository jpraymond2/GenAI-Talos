"""JSONL traces for every agent step, plus an in-process event queue."""

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ClassVar
from uuid import uuid4


class Tracer:
    """One run's trace file and a fan-out queue for live listeners."""

    _subscribers: ClassVar[list[asyncio.Queue[dict[str, Any]]]] = []

    def __init__(self, logs_dir: Path | str = "logs", run_id: str | None = None) -> None:
        """Open logs/<run_id>/trace.jsonl, creating the directory if needed."""
        self.run_id = run_id or str(uuid4())
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.path = Path(logs_dir) / self.run_id / "trace.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @classmethod
    def subscribe(cls) -> asyncio.Queue[dict[str, Any]]:
        """Return a queue that receives events from every tracer in this process."""
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        cls._subscribers.append(queue)
        return queue

    @classmethod
    def unsubscribe(cls, queue: asyncio.Queue[dict[str, Any]]) -> None:
        """Stop delivering events to a queue from subscribe()."""
        if queue in cls._subscribers:
            cls._subscribers.remove(queue)

    def event(self, agent: str, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Append one JSON object and broadcast it.

        Callers must keep secrets out of payload. The line is the on-disk record.
        """
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "run_id": self.run_id,
            "agent": agent,
            "kind": kind,
            "payload": payload,
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, default=str) + "\n")
        self.queue.put_nowait(record)
        for subscriber in list(self._subscribers):
            subscriber.put_nowait(record)
        return record
