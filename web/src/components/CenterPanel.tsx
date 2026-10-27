import { useState } from "react";
import type { Candidate, Evidence, PointVerdict, RunState, TalkingPoint } from "../types";
import { RecallScore } from "./RecallScore";
import { SourceChip } from "./SourceChip";

interface Props {
  run: RunState | null;
  onPickCandidate: (c: Candidate) => void;
  onConfirmActions: () => void;
  onCancelActions: () => void;
  busy: boolean;
}

export function CenterPanel({
  run,
  onPickCandidate,
  onConfirmActions,
  onCancelActions,
  busy,
}: Props) {
  if (!run) {
    return (
      <section className="flex h-full min-h-[420px] flex-col items-center justify-center rounded-2xl border border-dashed border-ink-300 bg-white/40 p-8 text-center shadow-panel">
        <p className="font-display text-3xl font-semibold text-ink-900">Your brief</p>
        <p className="mt-2 max-w-sm text-sm text-ink-500">
          Ask about a meeting and talking points will land here — with sources you can expand.
        </p>
      </section>
    );
  }

  if (run.status === "needs_clarification") {
    return (
      <section className="animate-rise flex h-full min-h-[420px] flex-col rounded-2xl border border-ink-200/80 bg-white/80 p-6 shadow-panel">
        <p className="text-xs font-semibold uppercase tracking-wider text-ember-600">
          Needs clarification
        </p>
        <h2 className="mt-3 font-display text-3xl font-semibold leading-tight text-ink-950">
          {run.question ?? "Who are you meeting?"}
        </h2>
        <div className="mt-8 grid gap-3 sm:grid-cols-2">
          {run.candidates.map((c) => (
            <button
              key={c.entity_id}
              type="button"
              disabled={busy}
              onClick={() => onPickCandidate(c)}
              className="rounded-2xl border border-ink-200 bg-gradient-to-b from-white to-ink-50 px-4 py-5 text-left transition hover:border-sea-500 hover:shadow-panel disabled:opacity-50"
            >
              <p className="font-display text-xl font-semibold text-ink-900">{c.name}</p>
              <p className="mt-1 text-sm text-ink-500">{c.reason}</p>
              <p className="mt-3 font-mono text-[11px] text-ink-400">
                score {c.score.toFixed(2)}
              </p>
            </button>
          ))}
        </div>
        {run.action_plan ? (
          <ActionBlock
            run={run}
            busy={busy}
            onConfirm={onConfirmActions}
            onCancel={onCancelActions}
          />
        ) : null}
        <div className="mt-auto pt-6">
          <RecallScore run={run} />
        </div>
      </section>
    );
  }

  const brief = run.final_brief ?? run.drafts.at(-1) ?? null;
  if (!brief) {
    return (
      <section className="flex h-full items-center justify-center rounded-2xl border border-ink-200 bg-white/70 p-6 shadow-panel">
        <p className="text-sm text-ink-500">Running…</p>
      </section>
    );
  }

  return (
    <section className="scroll-thin flex h-full min-h-[420px] flex-col overflow-y-auto rounded-2xl border border-ink-200/80 bg-white/80 p-5 shadow-panel md:p-6">
      <p className="text-xs font-semibold uppercase tracking-wider text-sea-600">Brief</p>
      <h2 className="mt-2 font-display text-3xl font-semibold leading-tight text-ink-950">
        {brief.headline}
      </h2>
      <p className="mt-2 text-sm text-ink-500">for {brief.person}</p>

      <div className="mt-6 space-y-4">
        {brief.points.map((point, index) => {
          const lastVerdict = run.verdicts.at(-1) ?? null;
          const pointVerdict =
            lastVerdict?.verdicts.find((v) => v.index === index) ?? null;
          return (
            <PointCard
              key={`${index}-${point.text.slice(0, 24)}`}
              point={point}
              index={index}
              evidence={run.evidence}
              pointVerdict={pointVerdict}
              conflict={
                point.kind === "logistics" &&
                (brief.caveats.some((c) => c.toLowerCase().includes("conflict")) ||
                  point.text.toLowerCase().includes("disagree") ||
                  point.text.toLowerCase().includes("conflict"))
              }
            />
          );
        })}
      </div>

      {brief.caveats.length ? (
        <ul className="mt-5 space-y-1 rounded-xl border border-ember-400/40 bg-ember-400/10 px-4 py-3 text-sm text-ember-600">
          {brief.caveats.map((c) => (
            <li key={c}>{c}</li>
          ))}
        </ul>
      ) : null}

      {run.action_plan && run.status === "awaiting_confirmation" ? (
        <ActionBlock
          run={run}
          busy={busy}
          onConfirm={onConfirmActions}
          onCancel={onCancelActions}
        />
      ) : null}

      <div className="mt-5">
        <RecallScore run={run} />
      </div>
    </section>
  );
}

function PointCard({
  point,
  index,
  evidence,
  conflict,
  pointVerdict,
}: {
  point: TalkingPoint;
  index: number;
  evidence: Record<string, Evidence>;
  conflict: boolean;
  pointVerdict: PointVerdict | null;
}) {
  const [open, setOpen] = useState<string | null>(null);
  const sources = point.evidence_ids
    .map((id) => evidence[id])
    .filter((e): e is Evidence => Boolean(e));

  return (
    <article className="animate-rise rounded-xl border border-ink-100 bg-ink-50/60 p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <p className="text-[11px] font-mono uppercase tracking-wide text-ink-400">
          {point.kind} · p{point.priority}
        </p>
        {conflict ? (
          <span className="rounded-md bg-ember-400/25 px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-ember-600">
            sources disagree
          </span>
        ) : null}
      </div>
      <p className="mt-2 text-[15px] leading-relaxed text-ink-900">{point.text}</p>
      <div className="mt-3 flex flex-wrap gap-2">
        {sources.map((ev) => (
          <SourceChip
            key={ev.id}
            evidence={ev}
            expanded={open === ev.id}
            onToggle={() => setOpen((cur) => (cur === ev.id ? null : ev.id))}
          />
        ))}
        {!sources.length ? (
          <span className="text-xs text-ink-400">No evidence chips</span>
        ) : null}
      </div>
      {pointVerdict ? (
        <p className="mt-2 text-xs leading-snug text-ink-500">
          <span className="font-mono font-semibold uppercase text-ink-700">
            {pointVerdict.decision}
          </span>
          {": "}
          {pointVerdict.reason}
        </p>
      ) : null}
      <span className="sr-only">point {index}</span>
    </article>
  );
}

function ActionBlock({
  run,
  busy,
  onConfirm,
  onCancel,
}: {
  run: RunState;
  busy: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const plan = run.action_plan;
  if (!plan) return null;
  return (
    <div className="mt-6 rounded-xl border border-ink-200 bg-white p-4">
      <p className="text-xs font-semibold uppercase tracking-wider text-ink-500">
        Pending actions
      </p>
      <p className="mt-1 text-sm text-ink-600">{plan.spoken_summary}</p>
      <ol className="mt-3 list-decimal space-y-2 pl-5 text-sm text-ink-800">
        {plan.steps.map((step, i) => (
          <li key={`${step.tool}-${i}`}>
            <span className="font-medium">{step.summary}</span>
            <span className="ml-2 font-mono text-[11px] text-ink-400">{step.tool}</span>
          </li>
        ))}
      </ol>
      {plan.requires_confirmation ? (
        <div className="mt-4 flex gap-2">
          <button
            type="button"
            disabled={busy}
            onClick={onConfirm}
            className="rounded-lg bg-sea-500 px-4 py-2 text-sm font-semibold text-white hover:bg-sea-600 disabled:opacity-50"
          >
            Confirm
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={onCancel}
            className="rounded-lg border border-ink-200 bg-white px-4 py-2 text-sm font-semibold text-ink-700 hover:bg-ink-50 disabled:opacity-50"
          >
            Cancel
          </button>
        </div>
      ) : null}
    </div>
  );
}
