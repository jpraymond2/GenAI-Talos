"""Checks for the synthetic Alex Martin sample corpus generator."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "data" / "sample"
SCRIPT = ROOT / "scripts" / "make_sample_data.py"
CSV_FILES = ("handles.csv", "interactions.csv", "events.csv", "contacts.csv")

ANCHOR = datetime(2026, 10, 15, 12, 0, 0, tzinfo=timezone.utc)


def _read_csv(sample_dir: Path, name: str) -> list[dict[str, str]]:
    path = sample_dir / name
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run_generator(out_dir: Path) -> None:
    env = os.environ.copy()
    env["SAMPLE_MODE"] = "true"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--out-dir", str(out_dir)],
        cwd=str(ROOT),
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr


@pytest.fixture(scope="module")
def sample_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Regenerate sample CSVs into a temp dir — never overwrite data/sample."""
    out = tmp_path_factory.mktemp("sample_data")
    cache_src = SAMPLE / "_text_cache.json"
    if cache_src.is_file():
        shutil.copy2(cache_src, out / "_text_cache.json")
    _run_generator(out)
    return out


def test_row_counts(sample_dir: Path) -> None:
    contacts = _read_csv(sample_dir, "contacts.csv")
    interactions = _read_csv(sample_dir, "interactions.csv")
    events = _read_csv(sample_dir, "events.csv")
    handles = _read_csv(sample_dir, "handles.csv")

    assert len(contacts) == 10
    assert len(interactions) == 400
    assert len(events) == 25
    assert len(handles) >= 15


def test_both_karims_present(sample_dir: Path) -> None:
    names = {row["full_name"] for row in _read_csv(sample_dir, "contacts.csv")}
    assert "Karim Haddad" in names
    assert "Karim Saleh" in names


def test_planted_fact_identity_merge_handles(sample_dir: Path) -> None:
    """Karim Haddad appears under Gmail, WhatsApp (flag nick), and iMessage phone."""
    handles = _read_csv(sample_dir, "handles.csv")
    by_value = {(h["kind"], h["value"]): h for h in handles}

    email = by_value[("email", "karim@ledgerly.io")]
    assert email["display_name"] == "Karim Haddad"

    wa = by_value[("wa_jid", "15145550142@s.whatsapp.net")]
    assert "Karim" in wa["display_name"]
    assert "\U0001f1f1\U0001f1e7" in wa["display_name"]

    imessage = by_value[("imessage", "+15145550142")]
    assert imessage["value"] == "+15145550142"


def test_planted_fact_montreal_vs_toronto_conflict(sample_dir: Path) -> None:
    interactions = _read_csv(sample_dir, "interactions.csv")
    montreal = [
        r
        for r in interactions
        if r["source"] == "gmail"
        and r["handle_value"] == "karim@ledgerly.io"
        and "Montreal" in r["text"]
    ]
    toronto = [
        r
        for r in interactions
        if r["source"] == "whatsapp"
        and r["handle_value"] == "+15145550142"
        and "Toronto" in r["text"]
        and "January" in r["text"]
    ]
    assert montreal, "expected ~2-month-old Gmail with Montreal signature"
    assert toronto, "expected WhatsApp saying moving to Toronto in January"

    montreal_ts = datetime.fromisoformat(montreal[0]["ts_utc"].replace("Z", "+00:00"))
    toronto_ts = datetime.fromisoformat(toronto[0]["ts_utc"].replace("Z", "+00:00"))
    assert abs((ANCHOR - montreal_ts).days - 60) <= 2
    assert abs((ANCHOR - toronto_ts).days - 12) <= 1


def test_planted_fact_open_loop_lina_intro(sample_dir: Path) -> None:
    interactions = _read_csv(sample_dir, "interactions.csv")
    intros = [
        r
        for r in interactions
        if r["source"] == "gmail"
        and r["handle_value"] == "karim@ledgerly.io"
        and r["direction"] == "in"
        and "Lina Chen" in r["text"]
        and r["thread_id"] == "th_kh_lina_intro"
    ]
    assert len(intros) == 1
    intro_ts = datetime.fromisoformat(intros[0]["ts_utc"].replace("Z", "+00:00"))
    assert abs((ANCHOR - intro_ts).days - 19) <= 1

    replies = [
        r
        for r in interactions
        if r["thread_id"] == "th_kh_lina_intro" and r["direction"] == "out"
    ]
    assert replies == [], "Alex must not have replied to the Lina intro ask"


def test_planted_fact_seed_round_whatsapp(sample_dir: Path) -> None:
    interactions = _read_csv(sample_dir, "interactions.csv")
    hits = [
        r
        for r in interactions
        if r["source"] == "whatsapp"
        and r["handle_value"] == "+15145550142"
        and "seed round" in r["text"].lower()
        and "internship" in r["text"].lower()
    ]
    assert hits, "expected WhatsApp about closing a seed round + internship"
    ts = datetime.fromisoformat(hits[0]["ts_utc"].replace("Z", "+00:00"))
    assert abs((ANCHOR - ts).days - 21) <= 1


def test_planted_fact_coffee_event_june(sample_dir: Path) -> None:
    events = _read_csv(sample_dir, "events.csv")
    coffee = [e for e in events if e["title"] == "Coffee w/ Karim"]
    assert len(coffee) == 1
    assert "Mile End" in coffee[0]["location"]
    start = datetime.fromisoformat(coffee[0]["start_utc"].replace("Z", "+00:00"))
    assert start.year == 2026 and start.month == 6
    attendees = json.loads(coffee[0]["attendees"])
    assert "karim@ledgerly.io" in attendees


def test_planted_fact_lina_chen_contact(sample_dir: Path) -> None:
    contacts = {c["full_name"]: c for c in _read_csv(sample_dir, "contacts.csv")}
    assert "Lina Chen" in contacts
    emails = json.loads(contacts["Lina Chen"]["emails"])
    assert len(emails) >= 2
    assert any("northbridge" in e for e in emails)

    interactions = _read_csv(sample_dir, "interactions.csv")
    lina_mail = [r for r in interactions if r["handle_value"] == "lina.chen@northbridge.vc"]
    assert len(lina_mail) >= 2


def test_csv_total_size_under_1mb(sample_dir: Path) -> None:
    total = sum((sample_dir / name).stat().st_size for name in CSV_FILES)
    assert total < 1_000_000, f"sample CSVs total {total} bytes"


def test_two_runs_produce_identical_files(sample_dir: Path) -> None:
    """Re-running the generator into the same tmp dir must not change bytes."""
    before = {name: _file_digest(sample_dir / name) for name in CSV_FILES}
    before_cache = _file_digest(sample_dir / "_text_cache.json")

    _run_generator(sample_dir)

    after = {name: _file_digest(sample_dir / name) for name in CSV_FILES}
    after_cache = _file_digest(sample_dir / "_text_cache.json")
    assert before == after
    assert before_cache == after_cache


def test_readme_exists(sample_dir: Path) -> None:
    readme = (sample_dir / "README.md").read_text(encoding="utf-8")
    assert "synthetic" in readme.lower() or "fictional" in readme.lower()
    assert "make_sample_data.py" in readme


def test_generator_does_not_touch_repo_sample(tmp_path: Path) -> None:
    """Regression: regenerating must not rewrite data/sample in the repo."""
    before = {name: _file_digest(SAMPLE / name) for name in CSV_FILES if (SAMPLE / name).is_file()}
    out = tmp_path / "isolated"
    out.mkdir()
    if (SAMPLE / "_text_cache.json").is_file():
        shutil.copy2(SAMPLE / "_text_cache.json", out / "_text_cache.json")
    _run_generator(out)
    after = {name: _file_digest(SAMPLE / name) for name in CSV_FILES if (SAMPLE / name).is_file()}
    assert before == after
