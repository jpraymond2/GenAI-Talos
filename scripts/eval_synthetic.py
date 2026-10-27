#!/usr/bin/env python3
"""Score all synthetic ground-truth questions; write eval_report.md + JSON."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
GT = ROOT / "data" / "synthetic" / "ground_truth"
OUT = ROOT / "output_examples"


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _brief_blob(state: Any) -> str:
    parts: list[str] = []
    if state.question:
        parts.append(state.question)
    for c in state.candidates or []:
        parts.append(c.name)
        parts.append(c.reason or "")
    brief = state.final_brief
    if brief:
        parts.append(brief.person or "")
        parts.append(brief.headline or "")
        for p in brief.points:
            parts.append(p.text)
            parts.extend(p.evidence_ids)
        parts.extend(brief.caveats or [])
    return " ".join(parts).lower()


def _covers(fact_id: str, text: str, keywords: dict[str, list[list[str]]]) -> bool:
    groups = keywords.get(fact_id) or []
    if not groups:
        return False
    for group in groups:
        if not any(str(kw).lower() in text for kw in group):
            return False
    return True


def _person_match(
    state: Any,
    expect_person: str,
    people: dict[str, Any],
) -> bool:
    person = people.get(expect_person) or {}
    want_name = str(person.get("name") or "").lower()
    labels = [str(x).lower() for x in (person.get("display_labels") or [])]
    got = (state.entity_name or "").lower()
    if not got and state.final_brief:
        got = (state.final_brief.person or "").lower()
    if want_name and want_name == got:
        return True
    if got and any(got == lab or got in lab or lab in got for lab in labels if lab):
        return True
    # First+last token overlap
    if want_name and got:
        wt = set(want_name.split())
        gt = set(got.split())
        if len(wt & gt) >= min(2, len(wt)):
            return True
    return False


def _clarify_match(
    state: Any,
    expect_ids: list[str],
    people: dict[str, Any],
) -> bool:
    names = {(c.name or "").lower() for c in (state.candidates or [])}
    for pid in expect_ids:
        person = people.get(pid) or {}
        pname = str(person.get("name") or "").lower()
        labels = [str(x).lower() for x in (person.get("display_labels") or [])]
        ok = False
        for cand in names:
            if pname and (pname == cand or pname in cand or cand in pname):
                ok = True
                break
            if any(lab and (lab in cand or cand in lab) for lab in labels):
                ok = True
                break
        if not ok:
            return False
    return True


async def _eval_one(
    question: dict[str, Any],
    *,
    deps: Any,
    people: dict[str, Any],
    keywords: dict[str, list[list[str]]],
) -> dict[str, Any]:
    from recall.orchestrator import run

    qid = question["id"]
    ask = question["ask"]
    expect = question.get("expect") or {}
    state = await run(ask, deps=deps)
    blob = _brief_blob(state)
    checks: dict[str, Any] = {}
    passed = True
    reasons: list[str] = []

    if "clarify" in expect:
        ok = state.status == "needs_clarification" and _clarify_match(
            state, list(expect["clarify"]), people
        )
        checks["clarify"] = ok
        if not ok:
            passed = False
            reasons.append("clarify mismatch")

    if "person" in expect:
        ok = _person_match(state, str(expect["person"]), people)
        checks["person"] = ok
        if not ok:
            passed = False
            reasons.append(f"person want={expect['person']} got={state.entity_name}")

    if expect.get("unknown") or question.get("category") == "unknown":
        ok = state.final_brief is None or not (state.final_brief.points if state.final_brief else [])
        # q17: no brief / unknown
        if "person" not in expect and "clarify" not in expect:
            ok = state.status == "needs_clarification" and not state.candidates
            if state.final_brief and state.final_brief.points:
                ok = False
        checks["unknown"] = ok
        if not ok:
            passed = False
            reasons.append("expected unknown")

    for fact_id in expect.get("must_cover") or []:
        ok = _covers(str(fact_id), blob, keywords)
        checks[f"cover:{fact_id}"] = ok
        if not ok:
            passed = False
            reasons.append(f"missing {fact_id}")

    for fact_id in expect.get("must_not") or []:
        # Violated when all keyword groups match OR evidence cited.
        covered = _covers(str(fact_id), blob, keywords)
        checks[f"must_not:{fact_id}"] = not covered
        if covered:
            passed = False
            reasons.append(f"leaked {fact_id}")

    if not checks and question.get("category") in {"calendar", "open_loops", "memory"}:
        # Unimplemented kinds may fail honestly.
        if state.final_brief is None and state.status != "needs_clarification":
            passed = False
            reasons.append("no brief (kind may be unimplemented)")

    return {
        "id": qid,
        "category": question.get("category"),
        "ask": ask,
        "passed": passed,
        "checks": checks,
        "reasons": reasons,
        "status": state.status,
        "entity_name": state.entity_name,
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--min-person",
        type=float,
        default=0.0,
        help="Regression floor for person accuracy (0–1).",
    )
    parser.add_argument(
        "--min-clarify",
        type=float,
        default=0.0,
        help="Regression floor for clarify accuracy (0–1).",
    )
    args = parser.parse_args()

    os.environ.setdefault("SAMPLE_MODE", "true")
    os.environ.setdefault("SAMPLE_DATA_DIR", "data/synthetic/tables")
    os.environ.setdefault("NVIDIA_API_KEY", "")

    from recall.config import get_settings
    from recall.orchestrator import build_deps

    get_settings.cache_clear()
    deps = build_deps(sample_mode=True)

    people = _load_json(GT / "people.json")
    keywords = _load_json(GT / "fact_keywords.json")
    questions = _load_json(GT / "questions.json")

    results: list[dict[str, Any]] = []
    for q in questions:
        row = await _eval_one(q, deps=deps, people=people, keywords=keywords)
        results.append(row)
        mark = "PASS" if row["passed"] else "FAIL"
        print(f"{mark} {row['id']} ({row['category']}) {', '.join(row['reasons'])}")

    by_cat: dict[str, list[bool]] = defaultdict(list)
    person_scores: list[bool] = []
    clarify_scores: list[bool] = []
    for row in results:
        by_cat[str(row["category"])].append(bool(row["passed"]))
        if "person" in row["checks"]:
            person_scores.append(bool(row["checks"]["person"]))
        if "clarify" in row["checks"]:
            clarify_scores.append(bool(row["checks"]["clarify"]))

    n = len(results)
    n_pass = sum(1 for r in results if r["passed"])
    overall = (100.0 * n_pass / n) if n else 0.0
    person_acc = (sum(person_scores) / len(person_scores)) if person_scores else 1.0
    clarify_acc = (sum(clarify_scores) / len(clarify_scores)) if clarify_scores else 1.0

    lines = [
        "# Synthetic eval report\n",
        f"**Overall: {n_pass}/{n} ({overall:.1f}%)**\n",
        f"- Person accuracy: {person_acc:.1%} ({sum(person_scores)}/{len(person_scores)})",
        f"- Clarify accuracy: {clarify_acc:.1%} ({sum(clarify_scores)}/{len(clarify_scores)})\n",
        "## Method\n",
        "The 23 questions were visible during development (not a held-out set). "
        "The code contains no dataset-specific literals (enforced by "
        "`tests/test_no_hardcoding.py`). Failures below are left honest — "
        "we do not chase 100%.\n",
        "## By category\n",
        "| category | pass | total | pct |",
        "|---|---:|---:|---:|",
    ]
    print("\n=== Score table ===")
    print(f"{'category':20} {'pass':>5} {'total':>5} {'pct':>7}")
    for cat in sorted(by_cat):
        vals = by_cat[cat]
        p = sum(vals)
        t = len(vals)
        pct = 100.0 * p / t if t else 0.0
        lines.append(f"| {cat} | {p} | {t} | {pct:.0f}% |")
        print(f"{cat:20} {p:5d} {t:5d} {pct:6.1f}%")
    print(f"{'OVERALL':20} {n_pass:5d} {n:5d} {overall:6.1f}%")
    print(f"person_acc={person_acc:.3f} clarify_acc={clarify_acc:.3f}")

    lines.append("\n## Per question\n")
    lines.append("| id | category | result | notes |")
    lines.append("|---|---|---|---|")
    for row in results:
        note = "; ".join(row["reasons"]) or "ok"
        mark = "pass" if row["passed"] else "fail"
        lines.append(f"| {row['id']} | {row['category']} | {mark} | {note} |")

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "eval_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    payload = {
        "overall_pct": overall,
        "passed": n_pass,
        "total": n,
        "person_accuracy": person_acc,
        "clarify_accuracy": clarify_acc,
        "results": results,
    }
    (OUT / "eval_results.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )
    print(f"\nwrote {OUT / 'eval_report.md'}")
    print(f"wrote {OUT / 'eval_results.json'}")

    if person_acc < args.min_person:
        raise SystemExit(f"person accuracy {person_acc:.3f} < floor {args.min_person}")
    if clarify_acc < args.min_clarify:
        raise SystemExit(f"clarify accuracy {clarify_acc:.3f} < floor {args.min_clarify}")


if __name__ == "__main__":
    asyncio.run(main())
