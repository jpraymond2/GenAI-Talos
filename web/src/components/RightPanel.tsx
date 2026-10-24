import { useEffect, useState } from "react";
import { fetchMemories, fetchPerson } from "../api";
import type { LedgerEvent, MemoryItem, PersonView, TraceEvent } from "../types";
import { TraceTimeline } from "./TraceTimeline";

type Tab = "trace" | "person" | "memory";

interface Props {
  events: TraceEvent[];
  entityId: string | null;
}

export function RightPanel({ events, entityId }: Props) {
  const [tab, setTab] = useState<Tab>("trace");
  const [person, setPerson] = useState<PersonView | null>(null);
  const [memories, setMemories] = useState<MemoryItem[]>([]);
  const [ledger, setLedger] = useState<LedgerEvent[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!entityId) {
      setPerson(null);
      setMemories([]);
      setLedger([]);
      return;
    }
    let cancelled = false;
    setLoading(true);
    void (async () => {
      const [p, m] = await Promise.all([fetchPerson(entityId), fetchMemories(entityId)]);
      if (cancelled) return;
      setPerson(p);
      setMemories(m.memories);
      setLedger(m.ledger);
      setLoading(false);
    })();
    return () => {
      cancelled = true;
    };
  }, [entityId]);

  return (
    <section className="flex h-full min-h-[420px] flex-col rounded-2xl border border-ink-200/80 bg-white/70 p-4 shadow-panel backdrop-blur-sm md:p-5">
      <div className="mb-4 flex gap-1 rounded-xl bg-ink-100 p-1">
        {(["trace", "person", "memory"] as Tab[]).map((id) => (
          <button
            key={id}
            type="button"
            onClick={() => setTab(id)}
            className={`flex-1 rounded-lg px-2 py-2 text-xs font-semibold uppercase tracking-wide transition ${
              tab === id
                ? "bg-white text-ink-900 shadow-sm"
                : "text-ink-500 hover:text-ink-800"
            }`}
          >
            {id}
          </button>
        ))}
      </div>

      <div className="scroll-thin flex-1 overflow-y-auto pr-1">
        {tab === "trace" ? <TraceTimeline events={events} /> : null}
        {tab === "person" ? (
          loading ? (
            <p className="text-sm text-ink-400">Loading person…</p>
          ) : person ? (
            <PersonTab person={person} />
          ) : (
            <p className="text-sm text-ink-400">No person selected yet.</p>
          )
        ) : null}
        {tab === "memory" ? (
          loading ? (
            <p className="text-sm text-ink-400">Loading memories…</p>
          ) : entityId ? (
            <MemoryTab memories={memories} ledger={ledger} />
          ) : (
            <p className="text-sm text-ink-400">Memories appear after a person is resolved.</p>
          )
        ) : null}
      </div>
    </section>
  );
}

function PersonTab({ person }: { person: PersonView }) {
  return (
    <div className="animate-rise space-y-4 text-sm">
      <div>
        <p className="font-display text-xl font-semibold text-ink-950">{person.canonical_name}</p>
        <p className="font-mono text-[11px] text-ink-400">{person.entity_id}</p>
      </div>
      <Block title="Aliases">
        <div className="flex flex-wrap gap-1.5">
          {person.aliases.map((a) => (
            <span key={a} className="rounded-md bg-ink-100 px-2 py-0.5 text-xs text-ink-700">
              {a}
            </span>
          ))}
        </div>
      </Block>
      <Block title="Merged sources">
        <div className="flex flex-wrap gap-1.5">
          {person.sources.length === 0 ? (
            <span className="text-xs text-ink-400">None yet</span>
          ) : (
            person.sources.map((s) => (
              <span
                key={s}
                className="rounded-md border border-sea-500/30 bg-sea-500/10 px-2 py-0.5 text-xs text-sea-600"
              >
                {s}
              </span>
            ))
          )}
        </div>
      </Block>
      <Block title="Attributes">
        <dl className="space-y-1">
          {Object.entries(person.attributes).map(([k, v]) => (
            <div key={k} className="flex justify-between gap-3">
              <dt className="text-ink-400">{k}</dt>
              <dd className="font-medium text-ink-800">{v}</dd>
            </div>
          ))}
        </dl>
      </Block>
      <Block title="Conflicts">
        {person.conflicts.length === 0 ? (
          <p className="text-xs text-ink-400">No conflicts</p>
        ) : (
          person.conflicts.map((c) => (
            <div
              key={c.field}
              className="rounded-lg border border-ember-400/40 bg-ember-400/10 p-3 text-ember-600"
            >
              <p className="text-xs font-semibold uppercase">{c.field}</p>
              <ul className="mt-1 space-y-1 text-xs">
                {c.values.map((v) => (
                  <li key={`${v.value}-${v.source_ref}`}>
                    <span className="font-medium">{v.value}</span>
                    <span className="ml-2 font-mono text-[10px] opacity-80">{v.source_ref}</span>
                  </li>
                ))}
              </ul>
            </div>
          ))
        )}
      </Block>
    </div>
  );
}

function MemoryTab({
  memories,
  ledger,
}: {
  memories: MemoryItem[];
  ledger: LedgerEvent[];
}) {
  return (
    <div className="animate-rise space-y-5 text-sm">
      <Block title="Memories">
        {memories.length === 0 ? (
          <p className="text-xs text-ink-400">No memories yet.</p>
        ) : (
          <ul className="space-y-3">
            {memories.map((m) => (
              <li key={m.id} className="rounded-lg border border-ink-100 bg-ink-50/80 p-3">
                <div className="flex items-baseline justify-between gap-2">
                  <p className="font-medium text-ink-900">{m.text}</p>
                  <span className="shrink-0 font-mono text-[11px] text-sea-600">
                    {m.score.toFixed(2)}
                  </span>
                </div>
                <p className="mt-1 text-xs text-ink-500">why: {m.why}</p>
              </li>
            ))}
          </ul>
        )}
      </Block>
      {ledger.length > 0 ? (
        <Block title="Ledger">
          <ul className="space-y-2">
            {ledger.map((e) => (
              <li key={e.id} className="border-l-2 border-ink-200 pl-3">
                <p className="text-[11px] font-mono uppercase text-ink-400">{e.kind}</p>
                <p className="text-ink-800">{e.summary}</p>
                <p className="text-[11px] text-ink-400">
                  {e.ts ? new Date(e.ts).toLocaleString() : ""}
                </p>
              </li>
            ))}
          </ul>
        </Block>
      ) : null}
    </div>
  );
}

function Block({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-ink-400">
        {title}
      </h3>
      {children}
    </div>
  );
}
