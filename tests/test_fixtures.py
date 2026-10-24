"""Fixture pack validation."""

from __future__ import annotations

import json
from pathlib import Path

from tests.helpers_fixtures import load_fixture_evidence
from recall.schemas import Evidence

_KARIM_PATH = Path(__file__).resolve().parent / "fixtures" / "evidence_karim.json"


def test_karim_fixture_file_has_fourteen_items() -> None:
    raw = json.loads(_KARIM_PATH.read_text(encoding="utf-8"))
    assert isinstance(raw, list)
    assert len(raw) == 14


def test_every_karim_fixture_item_validates_as_evidence() -> None:
    raw = json.loads(_KARIM_PATH.read_text(encoding="utf-8"))
    for item in raw:
        evidence = Evidence.model_validate(item)
        assert evidence.id
        assert evidence.source in {
            "gmail",
            "imessage",
            "whatsapp",
            "calendar",
            "web",
            "memory",
            "linkedin",
        }
        assert evidence.text


def test_load_fixture_evidence_for_karim() -> None:
    pack = load_fixture_evidence("Karim Haddad")
    assert set(pack) == {f"e{i}" for i in range(1, 15)}
    assert all(isinstance(item, Evidence) for item in pack.values())
    assert pack["e6"].url == "https://example.com/ledgerly-seed"
    assert "Ledgerly" in pack["e1"].text
    assert "Karim Saleh" in pack["e11"].text
    assert pack["e13"].source == "linkedin"
    assert pack["e13"].tags == ["job_change"]
    assert pack["e14"].tags == ["job_change"]
    assert "Northbridge" in pack["e14"].text


def test_load_fixture_evidence_unknown_person_is_empty() -> None:
    assert load_fixture_evidence("Someone Else") == {}
    assert load_fixture_evidence("") == {}
