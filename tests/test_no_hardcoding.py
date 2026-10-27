"""Fail if production code under recall/ embeds person/org/city fixtures."""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_RECALL = _ROOT / "recall"
_BANNED_LEGACY = [
    "karim",
    "ledgerly",
    "saleh",
    "lina chen",
    "evidence_karim",
    "tests/fixtures",
]
# Tokens that appear in contact fields but are too generic to ban.
_GENERIC_ALLOW = frozenset(
    {
        "partner",
        "founder",
        "manager",
        "product",
        "senior",
        "capital",
        "university",
        "landlord",
        "student",
        "associate",
        "principal",
        "incoming",
        "advisory",
        "ventures",
        "recruiting",
        "professor",
        "finance",
        "science",
        "data",
        "scientist",
        "innovation",
        "directrice",
        "analytics",
        "proprio",
        "propriétaire",
        "lessor",
        "owner",
        "mother",
        "father",
        "maman",
        "mère",
        "papa",
        "père",
    }
)


def _add_phrase(out: set[str], raw: str) -> None:
    text = (raw or "").strip()
    if len(text) < 4:
        return
    folded = text.casefold()
    if folded in _GENERIC_ALLOW:
        return
    out.add(folded)


def _add_person_name(out: set[str], name: str) -> None:
    text = (name or "").strip()
    if not text:
        return
    _add_phrase(out, text)
    parts = [p for p in re.findall(r"[A-Za-zÀ-öø-ÿ']+", text) if len(p) >= 4]
    for part in parts:
        folded = part.casefold()
        if folded not in _GENERIC_ALLOW:
            out.add(folded)


def _tokens_from_people_json(path: Path) -> set[str]:
    out: set[str] = set()
    if not path.is_file():
        return out
    data = json.loads(path.read_text(encoding="utf-8"))
    people = data.values() if isinstance(data, dict) else data
    for person in people:
        if not isinstance(person, dict):
            continue
        _add_person_name(out, str(person.get("name") or ""))
        for label in person.get("display_labels") or []:
            _add_person_name(out, str(label))
        _add_phrase(out, str(person.get("org") or ""))
        _add_phrase(out, str(person.get("city") or ""))
        card = person.get("contact_card") or {}
        if isinstance(card, dict):
            _add_phrase(out, str(card.get("org") or ""))
            _add_phrase(out, str(card.get("city") or ""))
    return out


def _tokens_from_contacts_csv(path: Path) -> set[str]:
    out: set[str] = set()
    if not path.is_file():
        return out
    with path.open(encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            _add_person_name(out, str(row.get("full_name") or row.get("name") or ""))
            _add_phrase(out, str(row.get("org") or ""))
            _add_phrase(out, str(row.get("city") or ""))
    return out


def _dataset_banned_tokens() -> set[str]:
    banned: set[str] = set()
    banned |= _tokens_from_people_json(
        _ROOT / "data" / "synthetic" / "ground_truth" / "people.json"
    )
    banned |= _tokens_from_contacts_csv(
        _ROOT / "data" / "synthetic" / "tables" / "contacts.csv"
    )
    banned |= _tokens_from_contacts_csv(_ROOT / "data" / "sample" / "contacts.csv")
    return banned


def test_no_hardcoding_in_recall_package() -> None:
    """Every .py under recall/ must avoid legacy person/fixture hardcoding."""
    pattern = re.compile("|".join(re.escape(b) for b in _BANNED_LEGACY), re.IGNORECASE)
    offenders: list[str] = []
    for path in sorted(_RECALL.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        match = pattern.search(text)
        if match:
            rel = path.relative_to(_ROOT)
            offenders.append(f"{rel}: matched {match.group(0)!r}")
    assert not offenders, "Hardcoded person/fixture strings in recall/:\n" + "\n".join(
        offenders
    )


def test_no_dataset_names_orgs_cities_in_recall() -> None:
    """Ban names / orgs / cities from synthetic+sample contacts in recall/**/*.py."""
    banned = _dataset_banned_tokens()
    assert banned, "expected dataset tokens to load"
    ordered = sorted(banned, key=len, reverse=True)
    pattern = re.compile(
        r"(?i)(?<![A-Za-z])("
        + "|".join(re.escape(t) for t in ordered)
        + r")(?![A-Za-z])"
    )
    offenders: list[str] = []
    for path in sorted(_RECALL.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        # Allow IANA timezone ids (America/Toronto) — not a dataset person/org.
        scrubbed = re.sub(r"America/[A-Za-z_]+", "America/TZ", text)
        match = pattern.search(scrubbed)
        if match:
            rel = path.relative_to(_ROOT)
            start = max(0, match.start() - 40)
            end = min(len(scrubbed), match.end() + 40)
            snippet = scrubbed[start:end].replace("\n", " ")
            offenders.append(f"{rel}: {match.group(1)!r} in …{snippet}…")
    assert not offenders, "Dataset literals in recall/:\n" + "\n".join(offenders)
