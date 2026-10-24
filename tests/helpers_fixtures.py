"""Test-only helpers for loading offline evidence packs."""

from __future__ import annotations

from pathlib import Path

from recall.fixtures import load_evidence_json
from recall.schemas import Evidence

_EVIDENCE_PACK = (
    Path(__file__).resolve().parent / "fixtures" / "evidence_karim.json"
)


def load_fixture_evidence(person_name: str) -> dict[str, Evidence]:
    """Load the sample evidence pack when the name matches; else empty."""
    if person_name.strip().lower() != "karim haddad":
        return {}
    return load_evidence_json(_EVIDENCE_PACK)
