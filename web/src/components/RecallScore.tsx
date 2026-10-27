import { useState } from "react";
import { judgeRun } from "../judge";
import type { RunState } from "../types";

const TONE: Record<string, { ring: string; text: string; bg: string }> = {
  Strong: { ring: "#2a7f74", text: "text-sea-600", bg: "bg-sea-500/10" },
  Good: { ring: "#3d9b8f", text: "text-sea-600", bg: "bg-sea-500/10" },
  Shaky: { ring: "#d4892a", text: "text-ember-600", bg: "bg-ember-400/15" },
  Weak: { ring: "#b56d1c", text: "text-ember-600", bg: "bg-ember-400/15" },
  Asked: { ring: "#6b857b", text: "text-ink-600", bg: "bg-ink-100" },
};

/** "How good was this recall?" — the judge's score, the one-line why, and the checks behind it. */
export function RecallScore({ run, compact = false }: { run: RunState; compact?: boolean }) {
  const [open, setOpen] = useState(false);
  if (run.status === "running" || run.status === "failed") return null;
  const j = judgeRun(run);
  const tone = TONE[j.grade] ?? TONE.Good;

  return (
    <section className={`rounded-2xl border border-ink-200/80 bg-white/90 ${compact ? "p-3" : "p-4"} shadow-panel`}>
      <button type="button" onClick={() => setOpen((v) => !v)} className="flex w-full items-center gap-3 text-left" aria-expanded={open}>
        <Ring value={j.score} color={tone.ring} size={compact ? 40 : 48} />
        <div className="min-w-0 flex-1">
          <p className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-wider text-ink-500">
            Recall judge
            <span className={`rounded px-1.5 py-px text-[10px] ${tone.bg} ${tone.text}`}>{j.grade}</span>
          </p>
          <p className="mt-0.5 text-sm leading-snug text-ink-800">{j.summary}</p>
        </div>
        {j.checks.length ? <span className="text-xs text-ink-400">{open ? "Hide" : "Why"}</span> : null}
      </button>

      {open && j.checks.length ? (
        <div className="mt-3 space-y-2 border-t border-ink-100 pt-3">
          {j.checks.map((c) => (
            <div key={c.id}>
              <div className="flex items-baseline justify-between gap-2 text-xs">
                <span className="font-semibold text-ink-800">{c.label}</span>
                <span className="font-mono text-ink-500">{c.score === null ? "n/a" : `${Math.round(c.score * 100)}%`}</span>
              </div>
              <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-ink-100">
                <div
                  className="h-full rounded-full transition-all"
                  style={{ width: `${c.score === null ? 0 : Math.round(c.score * 100)}%`, background: (c.score ?? 0) >= 0.8 ? "#2a7f74" : (c.score ?? 0) >= 0.5 ? "#e8a54b" : "#b56d1c" }}
                />
              </div>
              <p className="mt-0.5 text-[11px] text-ink-400">{c.detail}</p>
            </div>
          ))}
          {j.flags.length ? (
            <ul className="mt-2 space-y-1 rounded-lg bg-ember-400/10 px-3 py-2 text-xs text-ember-600">
              {j.flags.map((f) => (
                <li key={f}>⚠ {f}</li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

function Ring({ value, color, size }: { value: number | null; color: string; size: number }) {
  const r = (size - 6) / 2;
  const c = 2 * Math.PI * r;
  const pct = value === null ? 1 : value / 100;
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="shrink-0" aria-label={value === null ? "asked" : `${value} out of 100`}>
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="#e4e9e7" strokeWidth="5" />
      <circle
        cx={size / 2} cy={size / 2} r={r} fill="none" stroke={color} strokeWidth="5" strokeLinecap="round"
        strokeDasharray={`${c * pct} ${c}`} transform={`rotate(-90 ${size / 2} ${size / 2})`} strokeOpacity={value === null ? 0.35 : 1}
      />
      <text x="50%" y="50%" dominantBaseline="central" textAnchor="middle" fontSize={size * 0.3} fontWeight={700} fill="#252f2d" fontFamily="IBM Plex Mono, monospace">
        {value === null ? "?" : value}
      </text>
    </svg>
  );
}
