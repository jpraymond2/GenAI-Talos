"""Recall judge: after every answer, score how good the recall was.

No LLM and no answer key: it only looks at what the run produced, so it works
live, in sample mode and on the phone. Each check is explainable in one line:

  grounded      every talking point cites a message, and the message backs it up
  right person  Recall identified one person with a clear margin (or asked)
  fresh         the cited messages are recent, not year-old news
  breadth       it drew on several of the sources it had (mail, chats, calendar…)
  critic        the critic agent kept the points rather than dropping/fixing them
  useful        a short brief with something to act on (an open loop, logistics)

The score is the weighted average (0-100). Checks with nothing to judge are left
out and the weights re-normalised. ``scripts/synthetic/judge_recall.py`` is the
other half: it grades answers against the synthetic world's answer key.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

WEIGHTS = {
    "grounded": 0.30,
    "right_person": 0.20,
    "fresh": 0.15,
    "critic": 0.15,
    "breadth": 0.10,
    "useful": 0.10,
}
LABELS = {
    "grounded": "Backed by sources",
    "right_person": "Right person",
    "fresh": "Up to date",
    "critic": "Critic agreed",
    "breadth": "Used several sources",
    "useful": "Actionable",
}
CLOSE_MARGIN = 0.10
_STOP = {
    "about", "after", "again", "also", "been", "before", "from", "have", "just", "last", "meeting",
    "more", "next", "said", "should", "that", "their", "them", "then", "there", "they", "this",
    "with", "would", "your", "what", "when", "will", "were", "which", "into", "over", "still",
}


def judge_run(state: Any) -> dict[str, Any]:
    """Score one finished run (RunState or its JSON dict)."""
    data = state.model_dump(mode="json") if hasattr(state, "model_dump") else dict(state)
    status = data.get("status")
    candidates = data.get("candidates") or []

    if status == "needs_clarification":
        names = [c.get("name", "") for c in candidates]
        return {
            "score": None,
            "grade": "Asked",
            "summary": (
                f"Not sure who you meant, so it asked ({', '.join(n for n in names if n)}) instead of guessing."
                if len(names) >= 2 else "Asked a question instead of guessing."
            ),
            "checks": [],
            "flags": [],
        }

    brief = data.get("final_brief") or ((data.get("drafts") or [None])[-1])
    if not brief:
        return {"score": 0, "grade": "Weak", "summary": "No brief was produced.", "checks": [], "flags": ["no brief"]}

    evidence: dict[str, dict[str, Any]] = data.get("evidence") or {}
    points = brief.get("points") or []
    now = _parse_ts((data.get("utterance") or {}).get("ts")) or datetime.now(timezone.utc)
    flags: list[str] = []
    checks: dict[str, tuple[float | None, str]] = {}

    # grounded
    if points:
        per_point = []
        for i, p in enumerate(points, 1):
            cited = [evidence[e] for e in p.get("evidence_ids") or [] if e in evidence]
            if not cited:
                per_point.append(0.0)
                flags.append(f"point {i} has no source")
            elif _overlaps(p.get("text", ""), " ".join(str(c.get("text", "")) for c in cited)):
                per_point.append(1.0)
            else:
                per_point.append(0.6)
        good = sum(1 for s in per_point if s == 1.0)
        checks["grounded"] = (sum(per_point) / len(per_point), f"{good}/{len(points)} points match the messages they cite")
    else:
        checks["grounded"] = (0.0, "no talking points")

    # right person
    if data.get("entity_id"):
        scored = sorted((float(c.get("score") or 0) for c in candidates), reverse=True)
        if len(scored) >= 2 and scored[0] < 0.97 and scored[0] - scored[1] < CLOSE_MARGIN:
            checks["right_person"] = (0.5, f"picked {data.get('entity_name') or 'someone'} over a close second match")
            flags.append("close call on who you meant")
        else:
            checks["right_person"] = (1.0, f"identified {data.get('entity_name') or brief.get('person') or 'the person'}")
    elif brief.get("person") and brief.get("person") != "unknown":
        checks["right_person"] = (0.6, f"named {brief.get('person')} but not linked to a contact")
    else:
        checks["right_person"] = (0.0, "didn't work out who this is about")

    # fresh
    cited_ids = {e for p in points for e in (p.get("evidence_ids") or []) if e in evidence}
    ages = [(now - ts).days for ts in (_parse_ts(evidence[e].get("ts")) for e in cited_ids) if ts]
    if ages:
        fresh = sum(_freshness(a) for a in ages) / len(ages)
        newest = min(ages)
        checks["fresh"] = (fresh, f"newest source {_days(newest)}, median {_days(sorted(ages)[len(ages) // 2])}")
        if newest > 365:
            flags.append("everything cited is over a year old")
    else:
        checks["fresh"] = (None, "no dated sources")

    # breadth
    available = {str(e.get("source")) for e in evidence.values() if e.get("source")}
    used = {str(evidence[e].get("source")) for e in cited_ids}
    if available:
        checks["breadth"] = (min(1.0, len(used) / min(3, len(available))), f"cited {', '.join(sorted(used)) or 'nothing'} ({len(used)} of {len(available)} sources found)")
    else:
        checks["breadth"] = (None, "no sources fetched")

    # critic
    verdicts = data.get("verdicts") or []
    if verdicts:
        last = verdicts[-1]
        items = last.get("verdicts") or []
        kept = sum(1 for v in items if v.get("decision") == "keep")
        base = kept / len(items) if items else (1.0 if last.get("overall") == "pass" else 0.5)
        if last.get("overall") == "pass":
            base = max(base, 0.8)
        checks["critic"] = (base, f"{last.get('overall', '?')} · kept {kept}/{len(items)} points after {len(verdicts)} round(s)")
        if any(v.get("decision") == "conflict" for v in items):
            flags.append("sources disagree on something")
    else:
        checks["critic"] = (None, "critic didn't run")

    # useful
    kinds = {p.get("kind") for p in points}
    speech_words = len(str(data.get("speech") or "").split())
    useful = 0.0
    useful += 0.4 if 1 <= len(points) <= 6 else 0.15
    useful += 0.4 if kinds & {"open_loop", "logistics", "catch_up"} else 0.0
    useful += 0.2 if speech_words <= 90 else 0.0
    caveats = " ".join(brief.get("caveats") or []).lower()
    if "truncated" in caveats or "time budget" in caveats:
        useful = max(0.0, useful - 0.3)
        flags.append("answer was cut short by the time limit")
    checks["useful"] = (useful, f"{len(points)} points" + (", has an open loop" if "open_loop" in kinds else ""))

    rows, total, weight = [], 0.0, 0.0
    for key, w in WEIGHTS.items():
        value, detail = checks[key]
        rows.append({"id": key, "label": LABELS[key], "score": None if value is None else round(value, 2), "weight": w, "detail": detail})
        if value is not None:
            total += w * value
            weight += w
    score = round(100 * total / weight) if weight else 0
    grade = "Strong" if score >= 85 else "Good" if score >= 65 else "Shaky" if score >= 45 else "Weak"
    weakest = min((r for r in rows if r["score"] is not None), key=lambda r: r["score"], default=None)
    summary = (
        f"{grade} recall." if not weakest or weakest["score"] >= 0.8
        else f"{grade} recall — weakest: {weakest['label'].lower()} ({weakest['detail']})."
    )
    return {"score": score, "grade": grade, "summary": summary, "checks": rows, "flags": flags}


def _freshness(age_days: int) -> float:
    if age_days <= 30:
        return 1.0
    if age_days <= 90:
        return 0.75
    if age_days <= 365:
        return 0.45
    return 0.1


def _days(n: int) -> str:
    if n <= 0:
        return "today"
    if n < 60:
        return f"{n} d ago"
    return f"{round(n / 30)} mo ago"


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-zà-ÿ0-9$]{4,}", text.lower()) if w not in _STOP}


def _overlaps(point: str, sources: str) -> bool:
    return bool(_words(point) & _words(sources))


def _parse_ts(raw: Any) -> datetime | None:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
