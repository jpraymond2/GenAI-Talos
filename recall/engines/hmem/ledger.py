"""Append-only trust ledger for memory writes."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from recall.config import Settings, get_settings
from recall.engines.hmem.models import Memory
from recall.tracing import Tracer


class TrustLedgerService:
    """Write before/after snapshots to logs/<run_id>/memory_ledger.jsonl."""

    def __init__(
        self,
        settings: Settings | None = None,
        tracer: Tracer | None = None,
        run_id: str | None = None,
    ) -> None:
        self._settings = settings if settings is not None else get_settings()
        self._tracer = tracer if tracer is not None else Tracer.current()
        self._run_id = run_id or (self._tracer.run_id if self._tracer else "hmem-offline")
        self.path = Path(self._settings.logs_dir) / self._run_id / "memory_ledger.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(
        self,
        action: str,
        *,
        memory_id: str,
        actor: str = "hmem",
        before: dict[str, Any] | Memory | None = None,
        after: dict[str, Any] | Memory | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Append one ledger line and optionally emit a count-safe trace event."""
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "run_id": self._run_id,
            "actor": actor,
            "action": action,
            "memory_id": memory_id,
            "before": _snapshot(before),
            "after": _snapshot(after),
        }
        if extra:
            entry["extra"] = extra
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        if self._tracer is not None:
            self._tracer.event(
                "hmem",
                action,
                {"memory_id": memory_id, "actor": actor},
            )
        return entry


def record(kind: str, payload: dict[str, str]) -> None:
    """Legacy placeholder — use TrustLedgerService.record."""
    _ = (kind, payload)
    raise NotImplementedError("Use TrustLedgerService.record")


def _snapshot(value: dict[str, Any] | Memory | None) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, Memory):
        data = value.model_dump()
        # Keep ledger readable — omit bulky embeddings.
        data["embedding"] = f"<{len(value.embedding)} dims>"
        return data
    return dict(value)
