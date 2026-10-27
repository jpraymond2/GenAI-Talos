"""Judge Recall against the synthetic world's answer key: does it recall the right things?

    uv run python scripts/synthetic/judge_recall.py --live            # real agent (needs NVIDIA_API_KEY)
    uv run python scripts/synthetic/judge_recall.py --live --only q02,q05
    uv run python scripts/synthetic/judge_recall.py                   # offline sample model (smoke test only)

For each of the 23 questions in ground_truth/questions.json it asks the real
orchestrator (inside a throwaway copy of the synthetic world, never touching
data/world_sample.db), then grades the answer the way a person would:

  right person   did it brief on the person the question was about?
  covered        for each must-know fact, are its keywords in what Recall said?
                 (found in a fetched message but not said = quarter credit)
  no leaks       facts about a *different* same-name person must not appear
  asked          ambiguous questions ("I'm meeting Omar") should ask which one
  unknown        someone not in your world should get "I don't know", not a story

Prints a scorecard and writes data/synthetic/recall_report.md plus
logs/eval/recall_<timestamp>.json. Each answer also carries the live judge
score (recall/judge.py), so you can see whether the two agree.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

TZ = ZoneInfo("America/Toronto")
DONT_KNOW = re.compile(r"(don.?t (have|know|see)|no (record|history|messages|info)|couldn.?t find|not sure who|never (met|talked)|no one)", re.I)
WEIGHTS = {"person": 0.3, "covered": 0.6, "no_leaks": 0.1}


# --------------------------------------------------------------------------- grading (pure, tested)
def _norm(s: str) -> str:
    import unicodedata

    s = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


def fact_hit(keywords: list[list[str]], text: str) -> bool:
    """A fact is recalled when every keyword group has at least one hit."""
    low = _norm(text)
    return bool(keywords) and all(any(_norm(k) in low for k in group) for group in keywords)


def fact_share(keywords: list[list[str]], text: str) -> float:
    """Share of a fact's keyword groups that were said (Nov 6 but not 11am = half)."""
    low = _norm(text)
    if not keywords:
        return 0.0
    return sum(1 for group in keywords if any(_norm(k) in low for k in group)) / len(keywords)


def said_text(state: dict[str, Any]) -> str:
    brief = state.get("final_brief") or ((state.get("drafts") or [None])[-1]) or {}
    parts = [brief.get("headline", ""), *(p.get("text", "") for p in brief.get("points") or []),
             *(brief.get("caveats") or []), state.get("question") or "", state.get("speech") or ""]
    return "\n".join(p for p in parts if p)


def fetched_text(state: dict[str, Any]) -> str:
    return "\n".join(str(e.get("text", "")) for e in (state.get("evidence") or {}).values())


def same_person(got: str, want: dict[str, Any]) -> bool:
    g = _norm(got)
    if not g:
        return False
    names = [want.get("name", ""), *(want.get("display_labels") or [])]
    for n in names:
        n = _norm(re.sub(r"\(.*?\)", "", n)).strip()
        if n and (n == g or n in g or g in n) and len(min(n, g, key=len)) >= 3:
            # a bare first name only counts when it is the person's full label
            return len(g.split()) > 1 or len(n.split()) == 1 or g == n
    return False


def grade(q: dict[str, Any], state: dict[str, Any], people: dict[str, Any], keywords: dict[str, Any]) -> dict[str, Any]:
    """Score one answer 0..1 with a reason per part."""
    exp = q.get("expect") or {}
    said, fetched = said_text(state), fetched_text(state)
    parts: dict[str, float] = {}
    notes: list[str] = []
    who = state.get("entity_name") or ((state.get("final_brief") or {}).get("person")) or ""

    if "clarify" in exp:
        offered = [c.get("name", "") for c in state.get("candidates") or []]
        hits = sum(1 for p in exp["clarify"] if p in people and any(same_person(o, people[p]) for o in offered))
        if state.get("status") == "needs_clarification" and hits >= 2:
            return {"score": 1.0, "parts": {"asked": 1.0}, "notes": [f"asked which one ({', '.join(offered)})"]}
        if state.get("status") == "done" and exp["clarify"] and same_person(who, people[exp["clarify"][0]]):
            return {"score": 0.6, "parts": {"asked": 0.6}, "notes": [f"didn't ask, but picked the likeliest ({who})"]}
        return {"score": 0.0, "parts": {"asked": 0.0}, "notes": [f"should have asked; answered about {who or 'nobody'}"]}

    if not (set(exp) & {"person", "must_cover", "must_not"}):  # someone who isn't in your world
        ok = (not state.get("entity_id")) or state.get("status") == "needs_clarification" or bool(DONT_KNOW.search(said))
        return {"score": 1.0 if ok else 0.0, "parts": {"unknown": float(ok)},
                "notes": ["said it doesn't know them" if ok else f"made up a brief about {who}"]}

    if exp.get("person"):
        offered = [c.get("name", "") for c in state.get("candidates") or []]
        if state.get("status") == "needs_clarification" and len(offered) >= 2 and any(
                same_person(o, people[exp["person"]]) for o in offered):
            # Asked between real look-alikes, right person among them: a fair question, half marks.
            return {"score": 0.5, "parts": {"asked": 0.5},
                    "notes": [f"asked between {', '.join(offered)} — the context could have settled it"]}
        if state.get("status") == "needs_clarification":
            parts["person"] = 0.0
            notes.append("asked who you meant, without the right person as an option")
        else:
            ok = same_person(who, people[exp["person"]])
            parts["person"] = 1.0 if ok else 0.0
            if not ok:
                notes.append(f"wrong person: {who or 'none'} (wanted {people[exp['person']]['name']})")

    covers = []
    for fid in exp.get("must_cover") or []:
        kw = keywords.get(fid) or []
        share = fact_share(kw, said)
        if share == 1.0:
            covers.append(1.0)
        elif share > 0:
            covers.append(round(share, 2))
            notes.append(f"partly said: {fid}")
        elif fact_hit(kw, fetched):
            covers.append(0.25)
            notes.append(f"found but didn't say: {fid}")
        else:
            covers.append(0.0)
            notes.append(f"missed: {fid}")
    if covers:
        parts["covered"] = sum(covers) / len(covers)

    leaks = [fid for fid in exp.get("must_not") or [] if fact_hit(keywords.get(fid) or [], said)]
    if exp.get("must_not"):
        parts["no_leaks"] = 0.0 if leaks else 1.0
        notes += [f"mixed in someone else's fact: {fid}" for fid in leaks]

    weight = sum(WEIGHTS[k] for k in parts)
    score = sum(WEIGHTS[k] * v for k, v in parts.items()) / weight if weight else 0.0
    return {"score": round(score, 3), "parts": parts, "notes": notes or ["all good"]}


# --------------------------------------------------------------------------- running
def _load_env() -> None:
    env = ROOT / ".env"
    if not env.is_file():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _setup(root: Path, work: Path, live: bool) -> None:
    from scripts.synthetic.world import NOW

    now = datetime.strptime(NOW, "%Y-%m-%d %H:%M").replace(tzinfo=TZ)
    tables = (root / "tables").resolve()
    _load_env()
    os.environ.update(SAMPLE_MODE="true", DATA_BACKEND="duckdb", SAMPLE_DATA_DIR=str(tables), LOGS_DIR=str(work / "logs"))
    os.chdir(work)  # data/world_sample.db, H-MEM etc. land in the throwaway folder
    (work / "data").mkdir(exist_ok=True)

    from recall.config import get_settings

    get_settings.cache_clear()
    import recall.agents.time_context as tc
    import recall.data.queries as queries

    queries.SAMPLE_ANCHOR = now.astimezone(ZoneInfo("UTC"))
    tc.today_line = lambda *, now_=None, _n=now, **_: (  # the dataset's "today", not the real one
        f"TODAY: {_n.date().isoformat()}, {_n.strftime('%A')}, time zone America/Toronto")
    if live:
        import recall.llm.generator as gen

        if not get_settings().nvidia_api_key.get_secret_value():
            sys.exit("--live needs NVIDIA_API_KEY in .env")
        gen.use_sample_provider = lambda settings=None: False

    from recall.data.duckdb_backend import DuckDBBackend
    from recall.data.query_layer import get_query_layer
    from recall.engines.world.ingest import IngestPipelineService
    from recall.engines.world.store import WorldStore

    DuckDBBackend.clear_connection_cache()
    settings = get_settings()
    store = WorldStore(path=work / "data" / "world_sample.db", settings=settings)
    IngestPipelineService(store=store, settings=settings).run(get_query_layer(settings))
    store.close()
    from recall.llm.generator import reset_client

    reset_client()  # ingest may have opened the model client on its own event loop


async def _ask(text: str, timeout: float) -> dict[str, Any]:
    """Same path as the app's /api/ask (pre-routing + agent), without the 18 s phone time budget."""
    from recall.server.session import enrich_ask_payload, run_ask

    state = await run_ask(text, soft_timeout=timeout)
    return enrich_ask_payload(state)


async def _ask_all(questions: list[dict[str, Any]], people: dict[str, Any], keywords: dict[str, Any], timeout: float) -> list[dict[str, Any]]:
    results = []
    for q in questions:
        steps = [s.strip() for s in re.split(r"\bThen:\s*", q["ask"]) if s.strip()]
        t0 = time.perf_counter()
        try:
            state: dict[str, Any] = {}
            for step in steps:
                state = await _ask(step, timeout)
            g = grade(q, state, people, keywords)
        except Exception as exc:  # noqa: BLE001
            state, g = {}, {"score": 0.0, "parts": {}, "notes": [f"crashed: {type(exc).__name__}: {exc}"[:200]]}
        secs = time.perf_counter() - t0
        live_judge = (state.get("judge") or {}).get("score")
        results.append({"id": q["id"], "category": q["category"], "ask": q["ask"], "grade": g, "seconds": round(secs, 1),
                        "answer": said_text(state)[:600], "status": state.get("status"), "person": state.get("entity_name"),
                        "live_judge": live_judge})
        mark = "✓" if g["score"] >= 0.8 else "~" if g["score"] >= 0.4 else "✗"
        print(f"{mark} {q['id']} {round(100 * g['score']):>3}%  {q['ask'][:52]:<52}  {'; '.join(g['notes'])[:90]}", flush=True)
    return results


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", default=str(ROOT / "data" / "synthetic"))
    ap.add_argument("--live", action="store_true", help="use the real NVIDIA model (default: offline sample model)")
    ap.add_argument("--only", default="", help="comma-separated question ids, e.g. q02,q05")
    ap.add_argument("--timeout", type=float, default=90.0)
    args = ap.parse_args()

    root = Path(args.root).resolve()
    gt = root / "ground_truth"
    questions = json.loads((gt / "questions.json").read_text(encoding="utf-8"))
    people = json.loads((gt / "people.json").read_text(encoding="utf-8"))
    keywords = json.loads((gt / "fact_keywords.json").read_text(encoding="utf-8"))
    if args.only:
        wanted = {x.strip() for x in args.only.split(",")}
        questions = [q for q in questions if q["id"] in wanted]

    work = Path(tempfile.mkdtemp(prefix="recall_judge_"))
    cwd = Path.cwd()
    _setup(root, work, args.live)
    print(f"Synthetic world built in {work}. Asking {len(questions)} questions ({'live model' if args.live else 'offline sample model'})…\n")
    if not args.live:
        print("Note: the offline sample model only knows the canned demo questions, so most answers will fail.\n"
              "      Run with --live for a real score.\n")

    # One event loop for the whole run: the model client keeps connections bound to the
    # loop it was created on, so a fresh asyncio.run() per question fails with "Event loop is closed".
    results = asyncio.run(_ask_all(questions, people, keywords, args.timeout))

    os.chdir(cwd)
    total = round(100 * sum(r["grade"]["score"] for r in results) / max(1, len(results)))
    by_cat: dict[str, list[float]] = {}
    for r in results:
        by_cat.setdefault(r["category"], []).append(r["grade"]["score"])
    print(f"\nRecall score on the answer key: {total}%  ({sum(r['grade']['score'] >= 0.8 for r in results)}/{len(results)} answered well)")
    for cat, scores in sorted(by_cat.items()):
        print(f"  {cat:<15} {round(100 * sum(scores) / len(scores)):>3}%  ({len(scores)})")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = ROOT / "logs" / "eval"
    out.mkdir(parents=True, exist_ok=True)
    payload = {"total": total, "live_model": args.live, "by_category": {k: sum(v) / len(v) for k, v in by_cat.items()}, "results": results}
    (out / f"recall_{stamp}.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    _write_md(root / "recall_report.md", payload)
    print(f"\nWrote {root / 'recall_report.md'} and logs/eval/recall_{stamp}.json")


def _write_md(path: Path, payload: dict[str, Any]) -> None:
    lines = [
        "# Recall vs. the answer key",
        "",
        f"**Score: {payload['total']}%** on {len(payload['results'])} questions "
        f"({'live model' if payload['live_model'] else 'offline sample model — smoke test only'}). "
        "Generated by `scripts/synthetic/judge_recall.py`.",
        "",
        "| Category | Score |",
        "|---|---|",
        *[f"| {k} | {round(100 * v)}% |" for k, v in sorted(payload["by_category"].items())],
        "",
        "| # | Question | Score | Live judge | What happened |",
        "|---|---|---|---|---|",
    ]
    for r in payload["results"]:
        notes = "; ".join(r["grade"]["notes"]).replace("|", "/")
        lj = "—" if r["live_judge"] is None else f"{r['live_judge']}"
        lines.append(f"| {r['id']} | {r['ask'].replace('|', '/')} | {round(100 * r['grade']['score'])}% | {lj} | {notes} |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="")


if __name__ == "__main__":
    main()
