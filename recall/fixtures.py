"""Offline fixture helpers for tests (not used by the orchestrator).

Evidence packs live under tests/; this module only exposes a generic loader
so production code under recall/ stays free of person-specific paths.
"""

from __future__ import annotations

import json
from pathlib import Path

from recall.schemas import Evidence


def load_evidence_json(path: Path | str) -> dict[str, Evidence]:
    """Load a JSON list of Evidence objects from ``path``."""
    target = Path(path)
    raw = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"Fixture must be a JSON list: {target}")
    evidence = [Evidence.model_validate(item) for item in raw]
    return {item.id: item for item in evidence}
