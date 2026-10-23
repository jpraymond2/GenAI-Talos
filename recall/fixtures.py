"""Offline fixture packs used while the data layer is unfinished."""

from __future__ import annotations

import json
from pathlib import Path

from recall.schemas import Evidence

_KARIM_FIXTURE = (
    Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "evidence_karim.json"
)
_KARIM_NAMES = frozenset({"karim haddad"})


def load_fixture_evidence(person_name: str) -> dict[str, Evidence]:
    """Load fictional evidence for a person.

    Returns the Karim Haddad pack when the name matches; otherwise an empty dict.
    """
    if person_name.strip().lower() not in _KARIM_NAMES:
        return {}
    raw = json.loads(_KARIM_FIXTURE.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"Fixture must be a JSON list: {_KARIM_FIXTURE}")
    evidence = [Evidence.model_validate(item) for item in raw]
    return {item.id: item for item in evidence}
