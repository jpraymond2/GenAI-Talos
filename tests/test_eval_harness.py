"""Regression guard: eval harness runs and keeps person/clarify accuracy."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FLOOR_PATH = ROOT / "output_examples" / "eval_results.json"


def test_eval_harness_person_clarify_floor(tmp_path: Path) -> None:
    """Run eval; person and clarify accuracy must stay ≥ committed floors."""
    floors = {"person_accuracy": 0.5, "clarify_accuracy": 0.5}
    if FLOOR_PATH.is_file():
        committed = json.loads(FLOOR_PATH.read_text(encoding="utf-8"))
        floors["person_accuracy"] = float(committed.get("person_accuracy") or 0.5)
        floors["clarify_accuracy"] = float(committed.get("clarify_accuracy") or 0.5)
        # Allow small float noise but never raise the bar silently on first run.
        floors["person_accuracy"] = max(0.4, floors["person_accuracy"] - 0.05)
        floors["clarify_accuracy"] = max(0.4, floors["clarify_accuracy"] - 0.05)

    env = {
        **dict(**{k: v for k, v in __import__("os").environ.items()}),
        "SAMPLE_MODE": "true",
        "NVIDIA_API_KEY": "",
        "SAMPLE_DATA_DIR": "data/synthetic/tables",
    }
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "eval_synthetic.py"),
            "--min-person",
            str(floors["person_accuracy"]),
            "--min-clarify",
            str(floors["clarify_accuracy"]),
        ],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    assert FLOOR_PATH.is_file()
    payload = json.loads(FLOOR_PATH.read_text(encoding="utf-8"))
    assert payload["person_accuracy"] >= floors["person_accuracy"]
    assert payload["clarify_accuracy"] >= floors["clarify_accuracy"]
