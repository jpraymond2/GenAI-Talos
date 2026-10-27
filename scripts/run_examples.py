#!/usr/bin/env python3
"""Run input_examples/ in SAMPLE_MODE and write output_examples/<id>/."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "input_examples"
OUTPUT = ROOT / "output_examples"


def _brief_md(state: Any) -> str:
    lines: list[str] = []
    brief = state.final_brief
    if state.status == "needs_clarification":
        lines.append(f"# Clarification\n\n{state.question}\n")
        for i, c in enumerate(state.candidates, 1):
            lines.append(f"{i}. **{c.name}** — {c.reason}")
        return "\n".join(lines) + "\n"
    if brief is None:
        return f"# {state.status}\n\nNo brief.\n"
    lines.append(f"# {brief.person}\n")
    lines.append(f"**{brief.headline}**\n")
    for p in brief.points:
        chips = ", ".join(f"`{eid}`" for eid in p.evidence_ids) or "—"
        lines.append(f"- {p.text}  \n  sources: {chips}")
    if brief.caveats:
        lines.append("\n### Caveats")
        for c in brief.caveats:
            lines.append(f"- {c}")
    lines.append(f"\n_status: {state.status} · critic rounds: {state.round}_")
    return "\n".join(lines) + "\n"


async def _run_one(example: dict[str, Any], deps: Any) -> dict[str, Any]:
    from recall.orchestrator import run

    text = str(example["text"])
    t0 = time.perf_counter()
    state = await run(text, deps=deps)
    pick = example.get("pick")
    if state.status == "needs_clarification" and pick and state.candidates:
        idx = int(pick) - 1
        if 0 <= idx < len(state.candidates):
            chosen = state.candidates[idx]
            state = await run(
                text,
                run_id=state.run_id,
                clarification_reply=chosen.name,
                deps=deps,
            )
    followup = example.get("followup")
    if followup:
        state = await run(str(followup), deps=deps)
    latency_ms = int((time.perf_counter() - t0) * 1000)
    return {
        "state": state,
        "latency_ms": latency_ms,
        "critic_rounds": state.round,
        "status": state.status,
    }


async def main() -> None:
    os.environ.setdefault("SAMPLE_MODE", "true")
    os.environ.setdefault("SAMPLE_DATA_DIR", "data/synthetic/tables")
    os.environ.setdefault("NVIDIA_API_KEY", "")

    from recall.config import get_settings
    from recall.orchestrator import build_deps

    get_settings.cache_clear()
    deps = build_deps(sample_mode=True)

    examples = sorted(INPUT.glob("*.json"))
    rows: list[dict[str, Any]] = []
    for path in examples:
        example = json.loads(path.read_text(encoding="utf-8"))
        ex_id = str(example.get("id") or path.stem)
        out_dir = OUTPUT / ex_id
        if out_dir.exists():
            shutil.rmtree(out_dir)
        out_dir.mkdir(parents=True)

        result = await _run_one(example, deps)
        state = result["state"]
        (out_dir / "brief.md").write_text(_brief_md(state), encoding="utf-8")
        (out_dir / "run.json").write_text(
            state.model_dump_json(indent=2),
            encoding="utf-8",
        )
        # Copy latest trace if present.
        logs = Path(get_settings().logs_dir) / state.run_id
        trace_src = logs / "trace.jsonl"
        if trace_src.is_file():
            shutil.copy2(trace_src, out_dir / "trace.jsonl")
        else:
            (out_dir / "trace.jsonl").write_text("", encoding="utf-8")

        provider = "offline_rules"
        rows.append(
            {
                "id": ex_id,
                "text": example.get("text"),
                "why": example.get("why"),
                "status": result["status"],
                "critic_rounds": result["critic_rounds"],
                "provider": provider,
                "latency_ms": result["latency_ms"],
            }
        )
        print(f"wrote {out_dir}")

    readme = ["# Output examples\n", "| id | input | shows | rounds | provider | ms |", "|---|---|---|---|---|---|"]
    for row in rows:
        readme.append(
            f"| `{row['id']}` | {row['text']} | {row['why']} | "
            f"{row['critic_rounds']} | {row['provider']} | {row['latency_ms']} |"
        )
    (OUTPUT / "README.md").write_text("\n".join(readme) + "\n", encoding="utf-8")
    print(f"wrote {OUTPUT / 'README.md'}")


if __name__ == "__main__":
    asyncio.run(main())
