import type { PointVerdict, TraceEvent } from "../types";

const ORDER = [
  "router",
  "resolver",
  "retriever",
  "memory",
  "web",
  "writer",
  "critic",
  "orchestrator",
];

function sortKey(agent: string): number {
  const i = ORDER.indexOf(agent.toLowerCase());
  return i === -1 ? 50 : i;
}

function summaryLine(event: TraceEvent): string {
  const s = event.payload.summary;
  if (typeof s === "string" && s.trim()) return s;
  return `${event.agent}/${event.kind}`;
}

function retrieverBadge(event: TraceEvent): string | null {
  if (event.agent !== "retriever" && event.agent !== "query_layer") return null;
  const mode = event.payload.zetaris_mode;
  const backend = event.payload.backend;
  if (mode === "usl" || (backend === "zetaris" && mode !== "tables")) {
    return "via Zetaris USL";
  }
  if (backend === "duckdb" || event.payload.fallback === "duckdb") {
    return "fallback: DuckDB";
  }
  if (typeof event.payload.summary === "string" && event.payload.summary.includes("USL")) {
    return "via Zetaris USL";
  }
  return null;
}

interface Props {
  events: TraceEvent[];
}

export function TraceTimeline({ events }: Props) {
  const sorted = [...events].sort((a, b) => {
    const t = a.ts.localeCompare(b.ts);
    if (t !== 0) return t;
    return sortKey(a.agent) - sortKey(b.agent);
  });

  if (!sorted.length) {
    return (
      <p className="text-sm text-ink-400">
        Trace will fill as the run proceeds — router → resolver → retriever → …
      </p>
    );
  }

  return (
    <ol className="relative space-y-0 border-l border-ink-200 pl-4">
      {sorted.map((event, index) => {
        const model = typeof event.payload.model === "string" ? event.payload.model : null;
        const latency =
          typeof event.payload.latency_ms === "number"
            ? `${Math.round(event.payload.latency_ms)} ms`
            : null;
        const badge = retrieverBadge(event);
        const verdicts = Array.isArray(event.payload.verdicts)
          ? (event.payload.verdicts as PointVerdict[])
          : null;
        const overall =
          typeof event.payload.overall === "string" ? event.payload.overall : null;

        return (
          <li key={`${event.ts}-${event.agent}-${index}`} className="animate-rise relative pb-5">
            <span className="absolute -left-[21px] top-1.5 h-2.5 w-2.5 rounded-full border-2 border-sea-500 bg-white" />
            <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
              <span className="font-mono text-[11px] uppercase tracking-wide text-sea-600">
                {event.agent}
              </span>
              <span className="text-[11px] text-ink-400">{event.kind}</span>
              {overall ? (
                <span
                  className={`rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase ${
                    overall === "pass"
                      ? "bg-emerald-100 text-emerald-800"
                      : "bg-ember-400/20 text-ember-600"
                  }`}
                >
                  {overall}
                </span>
              ) : null}
            </div>
            <p className="mt-1 text-sm text-ink-800">{summaryLine(event)}</p>
            <div className="mt-1 flex flex-wrap gap-2 text-[11px] text-ink-400">
              {model ? <span className="font-mono">{model}</span> : null}
              {latency ? <span>{latency}</span> : null}
              {badge ? (
                <span className="rounded bg-sea-500/10 px-1.5 py-0.5 font-medium text-sea-600">
                  {badge}
                </span>
              ) : null}
            </div>
            {verdicts && event.agent === "critic" ? (
              <ul className="mt-2 space-y-1 rounded-md bg-ink-50 p-2 text-xs text-ink-600">
                {verdicts.map((v) => (
                  <li key={v.index}>
                    <span className="font-mono font-medium uppercase text-ink-800">
                      {v.decision}
                    </span>{" "}
                    · point {v.index}: {v.reason}
                  </li>
                ))}
              </ul>
            ) : null}
          </li>
        );
      })}
    </ol>
  );
}
