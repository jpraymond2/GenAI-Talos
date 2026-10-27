/** Client copy of recall/judge.py — used when the server didn't attach a score (demo mode, older server). */

import type { JudgeCheck, RecallJudge, RunState } from "./types";

const WEIGHTS: Record<string, number> = { grounded: 0.3, right_person: 0.2, fresh: 0.15, critic: 0.15, breadth: 0.1, useful: 0.1 };
const LABELS: Record<string, string> = {
  grounded: "Backed by sources",
  right_person: "Right person",
  fresh: "Up to date",
  critic: "Critic agreed",
  breadth: "Used several sources",
  useful: "Actionable",
};
const STOP = new Set(["about", "after", "again", "also", "been", "before", "from", "have", "just", "last", "meeting", "more", "next", "said",
  "should", "that", "their", "them", "then", "there", "they", "this", "with", "would", "your", "what", "when", "will", "were", "which", "into", "over", "still"]);

function words(t: string): Set<string> {
  return new Set((t.toLowerCase().match(/[a-zà-ÿ0-9$]{4,}/g) ?? []).filter((w) => !STOP.has(w)));
}

function freshness(days: number): number {
  return days <= 30 ? 1 : days <= 90 ? 0.75 : days <= 365 ? 0.45 : 0.1;
}

function ago(n: number): string {
  return n <= 0 ? "today" : n < 60 ? `${n} d ago` : `${Math.round(n / 30)} mo ago`;
}

export function judgeRun(run: RunState): RecallJudge {
  if (run.judge) return run.judge;
  if (run.status === "needs_clarification") {
    const names = run.candidates.map((c) => c.name).filter(Boolean);
    return {
      score: null,
      grade: "Asked",
      summary: names.length >= 2 ? `Not sure who you meant, so it asked (${names.join(", ")}) instead of guessing.` : "Asked a question instead of guessing.",
      checks: [],
      flags: [],
    };
  }
  const brief = run.final_brief ?? run.drafts.at(-1) ?? null;
  if (!brief) return { score: 0, grade: "Weak", summary: "No brief was produced.", checks: [], flags: ["no brief"] };

  const ev = run.evidence ?? {};
  const now = new Date(run.utterance?.ts ?? Date.now()).getTime();
  const flags: string[] = [];
  const checks: Record<string, [number | null, string]> = {};
  const points = brief.points;

  if (points.length) {
    let good = 0;
    const per: number[] = points.map((p, i): number => {
      const cited = p.evidence_ids.map((id) => ev[id]).filter(Boolean);
      if (!cited.length) {
        flags.push(`point ${i + 1} has no source`);
        return 0;
      }
      const src = words(cited.map((c) => c.text).join(" "));
      if ([...words(p.text)].some((w) => src.has(w))) {
        good += 1;
        return 1;
      }
      return 0.6;
    });
    checks.grounded = [per.reduce((a, b) => a + b, 0) / per.length, `${good}/${points.length} points match the messages they cite`];
  } else checks.grounded = [0, "no talking points"];

  const scores = run.candidates.map((c) => c.score).sort((a, b) => b - a);
  if (run.entity_id) {
    if (scores.length >= 2 && scores[0] < 0.97 && scores[0] - scores[1] < 0.1) {
      checks.right_person = [0.5, `picked ${run.entity_name ?? "someone"} over a close second match`];
      flags.push("close call on who you meant");
    } else checks.right_person = [1, `identified ${run.entity_name ?? brief.person}`];
  } else if (brief.person && brief.person !== "unknown") checks.right_person = [0.6, `named ${brief.person} but not linked to a contact`];
  else checks.right_person = [0, "didn't work out who this is about"];

  const cited = new Set(points.flatMap((p) => p.evidence_ids).filter((id) => ev[id]));
  const ages = [...cited].map((id) => Math.floor((now - new Date(ev[id].ts).getTime()) / 86400000)).filter((n) => !Number.isNaN(n));
  if (ages.length) {
    const sorted = [...ages].sort((a, b) => a - b);
    checks.fresh = [ages.reduce((a, n) => a + freshness(n), 0) / ages.length, `newest source ${ago(sorted[0])}, median ${ago(sorted[Math.floor(sorted.length / 2)])}`];
    if (sorted[0] > 365) flags.push("everything cited is over a year old");
  } else checks.fresh = [null, "no dated sources"];

  const available = new Set(Object.values(ev).map((e) => e.source));
  const used = new Set([...cited].map((id) => ev[id].source));
  checks.breadth = available.size
    ? [Math.min(1, used.size / Math.min(3, available.size)), `cited ${[...used].sort().join(", ") || "nothing"} (${used.size} of ${available.size} sources found)`]
    : [null, "no sources fetched"];

  const last = run.verdicts.at(-1);
  if (last) {
    const kept = last.verdicts.filter((v) => v.decision === "keep").length;
    let base = last.verdicts.length ? kept / last.verdicts.length : last.overall === "pass" ? 1 : 0.5;
    if (last.overall === "pass") base = Math.max(base, 0.8);
    checks.critic = [base, `${last.overall} · kept ${kept}/${last.verdicts.length} points after ${run.verdicts.length} round(s)`];
    if (last.verdicts.some((v) => v.decision === "conflict")) flags.push("sources disagree on something");
  } else checks.critic = [null, "critic didn't run"];

  const kinds = new Set(points.map((p) => p.kind));
  let useful = (points.length >= 1 && points.length <= 6 ? 0.4 : 0.15) + (["open_loop", "logistics", "catch_up"].some((k) => kinds.has(k as never)) ? 0.4 : 0);
  useful += (run.speech ?? "").split(/\s+/).filter(Boolean).length <= 90 ? 0.2 : 0;
  if (brief.caveats.join(" ").toLowerCase().match(/truncated|time budget/)) {
    useful = Math.max(0, useful - 0.3);
    flags.push("answer was cut short by the time limit");
  }
  checks.useful = [useful, `${points.length} points${kinds.has("open_loop") ? ", has an open loop" : ""}`];

  let total = 0;
  let weight = 0;
  const rows: JudgeCheck[] = Object.entries(WEIGHTS).map(([id, w]) => {
    const [v, detail] = checks[id];
    if (v !== null) {
      total += w * v;
      weight += w;
    }
    return { id, label: LABELS[id], score: v === null ? null : Math.round(v * 100) / 100, weight: w, detail };
  });
  const score = weight ? Math.round((100 * total) / weight) : 0;
  const grade = score >= 85 ? "Strong" : score >= 65 ? "Good" : score >= 45 ? "Shaky" : "Weak";
  const weakest = rows.filter((r) => r.score !== null).sort((a, b) => (a.score ?? 0) - (b.score ?? 0))[0];
  const summary = !weakest || (weakest.score ?? 1) >= 0.8 ? `${grade} recall.` : `${grade} recall — weakest: ${weakest.label.toLowerCase()} (${weakest.detail}).`;
  return { score, grade, summary, checks: rows, flags };
}
