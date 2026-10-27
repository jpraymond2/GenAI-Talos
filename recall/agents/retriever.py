"""Pull evidence for resolved entities. Thin wrapper over the orchestrator impl."""

from __future__ import annotations

from typing import Any

from recall.schemas import RunState


async def run(state: RunState, deps: Any) -> RunState:
    """Collect world / H-MEM / web evidence — delegates to orchestrator."""
    from recall.orchestrator import _retrieve

    return await _retrieve(state, deps)
