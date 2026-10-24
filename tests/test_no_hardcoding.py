"""Fail if production code under recall/ embeds person-specific fixtures."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1] / "recall"
_BANNED = [
    "karim",
    "ledgerly",
    "saleh",
    "lina chen",
    "evidence_karim",
    "tests/fixtures",
]
_PATTERN = re.compile("|".join(re.escape(b) for b in _BANNED), re.IGNORECASE)


def test_no_hardcoding_in_recall_package() -> None:
    """Every .py under recall/ must avoid person/fixture hardcoding."""
    offenders: list[str] = []
    for path in sorted(_ROOT.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        match = _PATTERN.search(text)
        if match:
            rel = path.relative_to(_ROOT.parent)
            offenders.append(f"{rel}: matched {match.group(0)!r}")
    assert not offenders, "Hardcoded person/fixture strings in recall/:\n" + "\n".join(
        offenders
    )
