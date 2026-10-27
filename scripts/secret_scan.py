"""Scan git history for obvious secrets. Prints PASS or FAIL.

Runs ``git log -p`` and flags sk-, nvapi-, tvly-, client_secret assignments,
and Zetaris1@. Ignores empty placeholders and common test fixtures.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_NVAPI = re.compile(r"\bnvapi-([A-Za-z0-9_\-]+)")
_SK = re.compile(r"\bsk-([A-Za-z0-9_\-]{8,})")
_TVLY = re.compile(r"\btvly-([A-Za-z0-9_\-]+)")
# Word-boundary so GOOGLE_CLIENT_SECRET= (empty) is not a hit.
_CLIENT_SECRET = re.compile(
    r"(?i)(?<![A-Za-z0-9_])client_secret[\"']?\s*[=:]\s*[\"']?([^\s\"'#=\n]+)"
)
_ZETARIS_DEFAULT = re.compile(r"Zetaris1@")

_SAFE_SUFFIXES = {
    "test",
    "testkey",
    "test-key",
    "ok",
    "abc",
    "pasted-by-mistake",
    "wrong-slot",
    "example",
    "your-key",
    "xxx",
    "changeme",
}


def _safe_token(token: str) -> bool:
    lowered = token.strip().lower().strip("\"'")
    if not lowered or lowered in {"\"\"", "''", "none", "null", "…", "..."}:
        return True
    if lowered.startswith("secretstr"):
        return True
    if lowered in _SAFE_SUFFIXES:
        return True
    if any(lowered.startswith(s) for s in ("test", "fake", "dummy", "example", "placeholder")):
        return True
    return False


def main() -> int:
    # Exclude this scanner from the blob so its own needle string is not a hit.
    proc = subprocess.run(
        [
            "git",
            "log",
            "-p",
            "--all",
            "--",
            ".",
            ":!scripts/secret_scan.py",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if proc.returncode != 0:
        print(f"FAIL: git log failed: {proc.stderr.strip() or proc.returncode}")
        return 1

    blob = proc.stdout
    hits: list[str] = []

    for match in _NVAPI.finditer(blob):
        if _safe_token(match.group(1)):
            continue
        hits.append(_snippet("nvapi-", blob, match.start(), match.end()))

    for match in _SK.finditer(blob):
        if _safe_token(match.group(1)):
            continue
        hits.append(_snippet("sk-", blob, match.start(), match.end()))

    for match in _TVLY.finditer(blob):
        if _safe_token(match.group(1)):
            continue
        hits.append(_snippet("tvly-", blob, match.start(), match.end()))

    for match in _CLIENT_SECRET.finditer(blob):
        value = match.group(1)
        if _safe_token(value):
            continue
        # Field definitions like google_client_secret: SecretStr = Field(...)
        if value in {"SecretStr", "str", "Field("}:
            continue
        hits.append(_snippet("client_secret", blob, match.start(), match.end()))

    for match in _ZETARIS_DEFAULT.finditer(blob):
        hits.append(_snippet("Zetaris1@", blob, match.start(), match.end()))

    if hits:
        print("FAIL")
        for line in hits[:40]:
            print(f"  {line}")
        if len(hits) > 40:
            print(f"  … and {len(hits) - 40} more")
        return 1

    print("PASS")
    return 0


def _snippet(label: str, blob: str, start: int, end: int) -> str:
    left = max(0, start - 60)
    right = min(len(blob), end + 40)
    text = blob[left:right].replace("\n", " ")
    return f"{label}: …{text}…"


if __name__ == "__main__":
    raise SystemExit(main())
