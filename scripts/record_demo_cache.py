"""Record real Nemotron responses into data/cache/llm for demo replay.

Requires SAMPLE_MODE=true, a real NVIDIA_API_KEY, RECORD_LLM_CACHE=true,
and the frozen sample clock. Runs every input_examples/*.json utterance and
every question in data/synthetic/ground_truth/questions.json.

Usage:
  SAMPLE_MODE=true RECORD_LLM_CACHE=true \\
    SAMPLE_DATA_DIR=data/synthetic/tables \\
    uv run python scripts/record_demo_cache.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _utterances() -> list[str]:
    texts: list[str] = []
    examples = ROOT / "input_examples"
    if examples.is_dir():
        for path in sorted(examples.glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("text"):
                texts.append(str(data["text"]))
            follow = data.get("followup") if isinstance(data, dict) else None
            if follow:
                texts.append(str(follow))
    questions = ROOT / "data" / "synthetic" / "ground_truth" / "questions.json"
    if questions.is_file():
        for item in json.loads(questions.read_text(encoding="utf-8")):
            ask = item.get("ask")
            if ask:
                texts.append(str(ask))
    # Dedupe preserving order.
    seen: set[str] = set()
    out: list[str] = []
    for text in texts:
        if text not in seen:
            seen.add(text)
            out.append(text)
    return out


async def _main() -> int:
    os.environ.setdefault("SAMPLE_MODE", "true")
    os.environ.setdefault("RECORD_LLM_CACHE", "true")
    os.environ.setdefault("SAMPLE_DATA_DIR", "data/synthetic/tables")
    from recall.config import get_settings
    from recall.orchestrator import build_deps, run

    get_settings.cache_clear()
    settings = get_settings()
    if not settings.nvidia_api_key.get_secret_value().strip():
        print("NVIDIA_API_KEY is required to record cache entries.", file=sys.stderr)
        return 1
    # Temporarily leave sample_mode so generate() hits NVIDIA, while still
    # writing cache via RECORD_LLM_CACHE. Use a fake sample_mode=False for LLM
    # but keep dataset paths — simplest: unset sample for the LLM path by
    # clearing sample_mode only when key present is already handled: use_sample
    # is true when sample_mode OR missing key. So we must set SAMPLE_MODE=false
    # for live calls while keeping SAMPLE_DATA_DIR for the world DB.
    os.environ["SAMPLE_MODE"] = "false"
    get_settings.cache_clear()
    # Re-point data dir + record flag.
    os.environ["SAMPLE_DATA_DIR"] = str(
        os.environ.get("SAMPLE_DATA_DIR") or "data/synthetic/tables"
    )
    os.environ["RECORD_LLM_CACHE"] = "true"
    # World still needs sample tables — force sample_mode true for ingest path
    # via build_deps(sample_mode=True) which sets SAMPLE_MODE=true again.
    # Instead: keep SAMPLE_MODE=true and temporarily patch use_sample_provider.
    os.environ["SAMPLE_MODE"] = "true"
    get_settings.cache_clear()

    from recall.llm import generator as llm

    original = llm.use_sample_provider

    def _live(_settings=None) -> bool:
        return False

    llm.use_sample_provider = _live  # type: ignore[assignment]
    try:
        deps = build_deps(sample_mode=True)
        for text in _utterances():
            print(f"recording: {text!r}")
            state = await run(text, deps=deps)
            print(f"  status={state.status} person={state.entity_name!r}")
    finally:
        llm.use_sample_provider = original  # type: ignore[assignment]
    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
