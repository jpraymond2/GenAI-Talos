"""The Recall judge scores answers from what the run produced — and rewards asking over guessing."""

from __future__ import annotations

from recall.judge import judge_run

NOW = "2026-10-26T22:00:00Z"


def _run(**over):
    base = {
        "status": "done",
        "utterance": {"text": "I'm meeting Omar", "ts": NOW, "mode": "text"},
        "entity_id": "p_omar",
        "entity_name": "Omar Haddad",
        "candidates": [],
        "evidence": {
            "ev1": {"id": "ev1", "source": "gmail", "ts": "2026-10-20T10:00:00Z", "text": "Can you intro me to Rachel Goldberg before our coffee?"},
            "ev2": {"id": "ev2", "source": "whatsapp", "ts": "2026-10-24T10:00:00Z", "text": "We closed the Series A last week!"},
            "ev3": {"id": "ev3", "source": "calendar", "ts": "2026-10-28T14:00:00Z", "text": "Coffee with Omar"},
        },
        "final_brief": {
            "person": "Omar Haddad",
            "headline": "Coffee with Omar Wednesday",
            "points": [
                {"text": "He asked for an intro to Rachel Goldberg — still unanswered.", "evidence_ids": ["ev1"], "kind": "open_loop", "priority": 1},
                {"text": "Congratulate him on closing the Series A.", "evidence_ids": ["ev2"], "kind": "catch_up", "priority": 2},
            ],
            "caveats": [],
        },
        "drafts": [],
        "verdicts": [{"overall": "pass", "verdicts": [{"index": 0, "decision": "keep"}, {"index": 1, "decision": "keep"}], "missing": [], "refetch": []}],
        "speech": "Coffee with Omar Wednesday. He asked for an intro to Rachel.",
    }
    base.update(over)
    return base


def test_good_brief_scores_strong() -> None:
    j = judge_run(_run())
    assert j["grade"] == "Strong" and j["score"] >= 85
    assert {c["id"] for c in j["checks"]} == {"grounded", "right_person", "fresh", "critic", "breadth", "useful"}


def test_uncited_and_stale_points_drag_it_down() -> None:
    run = _run()
    run["final_brief"]["points"][1]["evidence_ids"] = []
    run["evidence"]["ev1"]["ts"] = "2024-01-01T00:00:00Z"
    run["verdicts"] = []
    j = judge_run(run)
    assert j["score"] < judge_run(_run())["score"]
    assert any("no source" in f for f in j["flags"])


def test_close_call_is_flagged_and_asking_is_not_punished() -> None:
    close = judge_run(_run(candidates=[{"entity_id": "p_omar", "name": "Omar Haddad", "score": 0.61, "reason": ""},
                                       {"entity_id": "p_omar2", "name": "Omar Benali", "score": 0.58, "reason": ""}]))
    assert any("close call" in f for f in close["flags"])
    asked = judge_run(_run(status="needs_clarification", candidates=[{"name": "Omar Haddad"}, {"name": "Omar Benali"}]))
    assert asked["grade"] == "Asked" and asked["score"] is None


def test_runstate_object_and_no_brief() -> None:
    from recall.schemas import RunState, Utterance

    state = RunState(run_id="r1", utterance=Utterance(text="hi", ts=NOW, mode="text"), status="done")
    assert judge_run(state)["grade"] == "Weak"


def test_answer_key_grader() -> None:
    import json
    from pathlib import Path

    from scripts.synthetic.judge_recall import grade

    gt = Path(__file__).resolve().parents[1] / "data" / "synthetic" / "ground_truth"
    people = json.loads((gt / "people.json").read_text(encoding="utf-8"))
    keywords = json.loads((gt / "fact_keywords.json").read_text(encoding="utf-8"))
    questions = {q["id"]: q for q in json.loads((gt / "questions.json").read_text(encoding="utf-8"))}

    asked = {"status": "needs_clarification", "candidates": [{"name": "Omar Haddad"}, {"name": "Omar B (MBA)"}, {"name": "Omar Khalil"}]}
    assert grade(questions["q01"], asked, people, keywords)["score"] == 1.0

    wrong = {"status": "done", "entity_name": "Omar Khalil", "final_brief": {"headline": "Your landlord", "points": []}}
    assert grade(questions["q02"], wrong, people, keywords)["score"] < 0.4

    made_up = {"status": "done", "entity_id": "x", "entity_name": "Sarah Whitfield",
               "final_brief": {"headline": "Sarah loves sailing", "points": []}}
    assert grade(questions["q17"], made_up, people, keywords)["score"] == 0.0
    honest = {"status": "done", "final_brief": {"headline": "I don't have anything on Sarah Whitfield", "points": []}}
    assert grade(questions["q17"], honest, people, keywords)["score"] == 1.0
