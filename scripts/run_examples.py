#!/usr/bin/env python3
"""Run input_examples/ in SAMPLE_MODE and write output_examples/<id>/."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import time
import uuid
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


def _load_trace(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _event_ms(row: dict[str, Any], prev_ts: str | None) -> int | None:
    payload = row.get("payload") or {}
    if isinstance(payload.get("latency_ms"), (int, float)):
        return int(payload["latency_ms"])
    return None


def _how_it_worked_md(state: Any, trace: list[dict[str, Any]], *, ex_id: str) -> str:
    lines = [
        f"# How it worked — `{ex_id}`\n",
        f"Run `{state.run_id}` · status `{state.status}` · critic rounds `{state.round}`\n",
        "## Timeline\n",
    ]
    step = 0
    for row in trace:
        if row.get("run_id") and row.get("run_id") != state.run_id:
            continue
        step += 1
        agent = row.get("agent") or "?"
        kind = row.get("kind") or "?"
        payload = row.get("payload") or {}
        summary = payload.get("summary") or f"{agent}/{kind}"
        ms = _event_ms(row, None)
        ms_bit = f" · {ms} ms" if ms is not None else ""
        lines.append(f"{step}. **{agent}** `{kind}`{ms_bit} — {summary}")

    lines.append("\n## Candidate choice\n")
    if state.candidates:
        for i, c in enumerate(state.candidates, 1):
            mark = "← chosen" if c.entity_id == state.entity_id else ""
            lines.append(
                f"{i}. **{c.name}** score={c.score:.2f} — {c.reason} {mark}".rstrip()
            )
    elif state.entity_name:
        lines.append(
            f"Resolved **{state.entity_name}** (`{state.entity_id}`) without clarification."
        )
    else:
        lines.append("No person resolved.")

    lines.append("\n## Sources & backend\n")
    backends = []
    for row in trace:
        payload = row.get("payload") or {}
        if row.get("agent") == "query_layer" and payload.get("backend"):
            backends.append(
                f"- `{payload.get('source_tag')}` → {payload.get('backend')} "
                f"({payload.get('row_count')} rows)"
            )
        if row.get("kind") == "fallback":
            backends.append(
                f"- fallback: {payload.get('from_backend')} → {payload.get('to_backend')} "
                f"({payload.get('reason')})"
            )
    lines.extend(backends[:12] or ["- (no query_layer events)"])

    lines.append("\n## Memories used\n")
    mems = list(state.memories or [])
    if not mems:
        lines.append("- none")
    for m in mems[:5]:
        mid = m.get("id") if isinstance(m, dict) else getattr(m, "id", "?")
        score = m.get("score") if isinstance(m, dict) else getattr(m, "score", "?")
        content = m.get("content") if isinstance(m, dict) else getattr(m, "content", "")
        lines.append(f"- `{mid}` score={score}: {str(content)[:120]}")

    lines.append("\n## Critic verdicts\n")
    for ri, verdict in enumerate(state.verdicts or [], 1):
        lines.append(f"### Round {ri} — overall `{verdict.overall}`\n")
        for pv in verdict.verdicts:
            lines.append(f"- point {pv.index} **{pv.decision}**: {pv.reason}")
        if verdict.missing:
            lines.append(f"- missing: {', '.join(verdict.missing)}")
        if verdict.refetch:
            lines.append(f"- refetch: {', '.join(verdict.refetch)}")
        lines.append("")

    if len(state.drafts or []) >= 2:
        lines.append("## Round-1 vs round-2 diff\n")
        from recall.orchestrator import _brief_point_diff

        diff = _brief_point_diff(state.drafts[0], state.drafts[-1])
        for label in ("added", "dropped", "changed"):
            items = diff.get(label) or []
            lines.append(f"### {label.title()} ({len(items)})")
            if not items:
                lines.append("- —")
            for item in items:
                eids = ", ".join(f"`{e}`" for e in (item.get("evidence_ids") or []))
                if label == "changed":
                    lines.append(f"- {eids}: {item.get('from', '')[:80]} → {item.get('to', '')[:80]}")
                else:
                    lines.append(f"- {eids}: {item.get('text', '')[:120]}")
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


async def _run_one(example: dict[str, Any], *, logs_dir: Path, run_id: str) -> dict[str, Any]:
    from recall.orchestrator import build_deps, run

    deps = build_deps(sample_mode=True, logs_dir=logs_dir, run_id=run_id)
    text = str(example["text"])
    t0 = time.perf_counter()
    state = await run(text, deps=deps, run_id=run_id)
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
        # Same run_id so note→brief share one isolated trace folder.
        state = await run(str(followup), deps=deps, run_id=run_id)
    latency_ms = int((time.perf_counter() - t0) * 1000)
    return {
        "state": state,
        "latency_ms": latency_ms,
        "critic_rounds": state.round,
        "status": state.status,
        "run_id": run_id,
    }


async def main() -> None:
    os.environ.setdefault("SAMPLE_MODE", "true")
    os.environ.setdefault("SAMPLE_DATA_DIR", "data/synthetic/tables")
    os.environ.setdefault("NVIDIA_API_KEY", "")

    from recall.config import get_settings

    get_settings.cache_clear()
    base_logs = Path(get_settings().logs_dir)

    examples = sorted(INPUT.glob("*.json"))
    rows: list[dict[str, Any]] = []
    for path in examples:
        example = json.loads(path.read_text(encoding="utf-8"))
        ex_id = str(example.get("id") or path.stem)
        out_dir = OUTPUT / ex_id
        if out_dir.exists():
            shutil.rmtree(out_dir)
        out_dir.mkdir(parents=True)

        run_id = f"example-{ex_id}-{uuid.uuid4().hex[:8]}"
        result = await _run_one(example, logs_dir=base_logs, run_id=run_id)
        state = result["state"]
        (out_dir / "brief.md").write_text(_brief_md(state), encoding="utf-8")
        (out_dir / "run.json").write_text(
            state.model_dump_json(indent=2),
            encoding="utf-8",
        )

        logs = base_logs / state.run_id
        # Copy only this run's artifacts (isolated run_id).
        for name in (
            "trace.jsonl",
            "state.json",
            "memory_ledger.jsonl",
            "actions_dryrun.json",
            "world_provenance.jsonl",
            "identity_ledger.jsonl",
            "merge_decisions.jsonl",
        ):
            src = logs / name
            if src.is_file():
                shutil.copy2(src, out_dir / name)
        if not (out_dir / "trace.jsonl").is_file():
            (out_dir / "trace.jsonl").write_text("", encoding="utf-8")

        trace = _load_trace(out_dir / "trace.jsonl")
        # Guard: every event must belong to this run.
        foreign = [r for r in trace if r.get("run_id") and r.get("run_id") != state.run_id]
        if foreign:
            trace = [r for r in trace if r.get("run_id") == state.run_id]
            (out_dir / "trace.jsonl").write_text(
                "\n".join(json.dumps(r, default=str) for r in trace) + ("\n" if trace else ""),
                encoding="utf-8",
            )
        (out_dir / "how_it_worked.md").write_text(
            _how_it_worked_md(state, trace, ex_id=ex_id),
            encoding="utf-8",
        )

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

    readme = [
        "# Output examples\n",
        "Each folder has `brief.md`, `run.json`, `trace.jsonl` (this run only), "
        "and [`how_it_worked.md`](./01_three_omars/how_it_worked.md) (timeline + critic).\n",
        "| id | input | shows | rounds | provider | ms | how |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        readme.append(
            f"| `{row['id']}` | {row['text']} | {row['why']} | "
            f"{row['critic_rounds']} | {row['provider']} | {row['latency_ms']} | "
            f"[how](./{row['id']}/how_it_worked.md) |"
        )
    (OUTPUT / "README.md").write_text("\n".join(readme) + "\n", encoding="utf-8")
    print(f"wrote {OUTPUT / 'README.md'}")


if __name__ == "__main__":
    asyncio.run(main())
