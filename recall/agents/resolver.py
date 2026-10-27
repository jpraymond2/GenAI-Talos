"""Resolve people named in the turn. Thin wrapper over the orchestrator impl."""

from __future__ import annotations

from typing import Any

from recall.schemas import RunState


def run(state: RunState, deps: Any) -> RunState:
    """Resolve person via handle or world.resolve — delegates to orchestrator."""
    from recall.orchestrator import _resolve

    return _resolve(state, deps)
