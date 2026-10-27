import { useEffect, useMemo, useState } from "react";
import { fetchUpcoming } from "../workspaceApi";
import type { CalendarEvent } from "../types";
import { Avatar, Spinner } from "./ui";

export interface PrepRequest {
  text: string;
  entityId: string | null;
}

/** Turn a meeting into what you'd say to Recall: "I'm meeting Omar Haddad Wednesday at 10:00". */
export function prepFor(ev: CalendarEvent): PrepRequest {
  const known = ev.attendees.filter((a) => a.entity_id && a.name);
  const when = `${dayWord(ev.start)} at ${time(ev.start)}`;
  if (known.length === 1) return { text: `I'm meeting ${known[0].name} ${when}`, entityId: known[0].entity_id };
  if (known.length > 1) return { text: `Prep me for "${ev.title}" ${when} with ${known.map((a) => a.name).join(", ")}`, entityId: null };
  return { text: `Prep me for "${ev.title}" ${when}`, entityId: null };
}

function useUpcoming(days: number) {
  const [events, setEvents] = useState<CalendarEvent[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    fetchUpcoming(days)
      .then((r) => alive && setEvents(r.events))
      .catch((e) => alive && (setError(e instanceof Error ? e.message : "Calendar unavailable"), setEvents([])));
    return () => {
      alive = false;
    };
  }, [days]);
  return { events, error };
}

interface PageProps {
  onPrep: (req: PrepRequest) => void;
  onOpenContact: (entityId: string) => void;
  onConnect: () => void;
}

export function CalendarPage({ onPrep, onOpenContact, onConnect }: PageProps) {
  const [days, setDays] = useState(14);
  const { events, error } = useUpcoming(days);
  const groups = useMemo(() => {
    const out = new Map<string, CalendarEvent[]>();
    for (const ev of events ?? []) {
      const key = new Date(ev.start).toDateString();
      out.set(key, [...(out.get(key) ?? []), ev]);
    }
    return [...out.entries()];
  }, [events]);

  return (
    <div className="mx-auto max-w-3xl space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="font-display text-3xl font-semibold text-ink-950">Upcoming</h1>
          <p className="text-sm text-ink-500">Your meetings, with the people Recall knows. Tap Prep before you walk in.</p>
        </div>
        <div className="flex rounded-xl bg-ink-100 p-1">
          {[7, 14, 30].map((d) => (
            <button
              key={d}
              type="button"
              onClick={() => setDays(d)}
              className={`rounded-lg px-3 py-1.5 text-xs font-semibold transition ${days === d ? "bg-white text-ink-950 shadow-sm" : "text-ink-500 hover:text-ink-800"}`}
            >
              {d} days
            </button>
          ))}
        </div>
      </div>

      {events === null ? (
        <div className="flex items-center gap-2 py-10 text-sm text-ink-500"><Spinner /> Loading your calendar…</div>
      ) : events.length === 0 ? (
        <section className="rounded-2xl border border-dashed border-ink-300 bg-white/50 p-8 text-center">
          <p className="font-display text-xl font-semibold text-ink-900">Nothing on the calendar</p>
          <p className="mt-1 text-sm text-ink-500">{error ?? `No meetings in the next ${days} days — or your calendar isn't connected yet.`}</p>
          <button type="button" onClick={onConnect} className="mt-4 rounded-lg bg-ink-900 px-4 py-2 text-sm font-semibold text-white hover:bg-ink-800">
            Check connections
          </button>
        </section>
      ) : (
        groups.map(([day, list]) => (
          <section key={day}>
            <p className="mb-2 mt-5 text-[11px] font-semibold uppercase tracking-wider text-ink-500">{dayHeading(list[0].start)}</p>
            <div className="space-y-3">
              {list.map((ev) => (
                <EventCard key={ev.id} ev={ev} onPrep={onPrep} onOpenContact={onOpenContact} />
              ))}
            </div>
          </section>
        ))
      )}
    </div>
  );
}

function EventCard({ ev, onPrep, onOpenContact }: { ev: CalendarEvent; onPrep: (r: PrepRequest) => void; onOpenContact: (id: string) => void }) {
  const soon = new Date(ev.start).getTime() - Date.now() < 3 * 3600e3;
  const live = new Date(ev.start).getTime() <= Date.now();
  return (
    <article className={`animate-rise flex gap-4 rounded-2xl border bg-white/90 p-4 shadow-panel ${soon ? "border-sea-500/50" : "border-ink-200/80"}`}>
      <div className="w-[4.5rem] shrink-0 whitespace-nowrap pt-0.5 text-right">
        <p className="font-mono text-sm font-semibold text-ink-900">{time(ev.start)}</p>
        <p className="font-mono text-[11px] text-ink-400">{time(ev.end)}</p>
      </div>
      <div className="min-w-0 flex-1 border-l border-ink-100 pl-4">
        <div className="flex flex-wrap items-center gap-2">
          <p className="font-semibold text-ink-950">{ev.title}</p>
          {live ? <Badge tone="sea">Now</Badge> : soon ? <Badge tone="ember">Soon</Badge> : null}
        </div>
        {ev.location ? <p className="mt-0.5 truncate text-xs text-ink-500">{ev.location}</p> : null}
        {ev.attendees.length ? (
          <div className="mt-2.5 flex flex-wrap gap-1.5">
            {ev.attendees.map((a) =>
              a.entity_id ? (
                <button
                  key={`${a.entity_id}-${a.email}`}
                  type="button"
                  onClick={() => onOpenContact(a.entity_id!)}
                  className="flex items-center gap-1.5 rounded-full bg-ink-50 py-0.5 pl-0.5 pr-2.5 text-xs font-medium text-ink-800 hover:bg-ink-100"
                >
                  <span className="scale-75"><Avatar name={a.name} size="sm" /></span>
                  {a.name}
                </button>
              ) : (
                <span key={a.email} className="rounded-full bg-ink-50 px-2.5 py-1 text-xs text-ink-400">{a.email}</span>
              ),
            )}
          </div>
        ) : null}
      </div>
      <button
        type="button"
        onClick={() => onPrep(prepFor(ev))}
        className="h-fit shrink-0 self-center rounded-xl bg-sea-600 px-3.5 py-2 text-xs font-semibold text-white shadow-sm transition hover:bg-sea-500"
      >
        Prep me
      </button>
    </article>
  );
}

/** "Next up" strip for the assistant screen (desktop right column, phone empty state). */
export function NextUp({ onPrep, limit = 3 }: { onPrep: (r: PrepRequest) => void; limit?: number }) {
  const { events } = useUpcoming(7);
  const next = (events ?? []).slice(0, limit);
  if (!next.length) return null;
  return (
    <section className="rounded-2xl border border-ink-200/80 bg-white/80 p-4 shadow-panel">
      <p className="text-[11px] font-semibold uppercase tracking-wider text-ink-500">Next up</p>
      <ul className="mt-2 divide-y divide-ink-100">
        {next.map((ev) => (
          <li key={ev.id} className="flex items-center gap-3 py-2.5">
            <div className="w-[4.25rem] shrink-0 whitespace-nowrap">
              <p className="text-[11px] font-semibold uppercase text-ink-400">{shortDay(ev.start)}</p>
              <p className="font-mono text-sm font-semibold text-ink-900">{time(ev.start)}</p>
            </div>
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-semibold text-ink-900">{ev.title}</p>
              <p className="truncate text-xs text-ink-500">{ev.attendees.filter((a) => a.name).map((a) => a.name).join(", ") || ev.location || "—"}</p>
            </div>
            <button type="button" onClick={() => onPrep(prepFor(ev))} className="shrink-0 rounded-lg border border-sea-500/40 px-2.5 py-1 text-xs font-semibold text-sea-600 hover:bg-sea-500/10">
              Prep
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}

function Badge({ tone, children }: { tone: "sea" | "ember"; children: React.ReactNode }) {
  return (
    <span className={`rounded px-1.5 py-px text-[10px] font-semibold uppercase tracking-wide ${tone === "sea" ? "bg-sea-500/15 text-sea-600" : "bg-ember-400/20 text-ember-600"}`}>
      {children}
    </span>
  );
}

function time(iso: string): string {
  return new Date(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

function daysFromToday(iso: string): number {
  const a = new Date(iso);
  const b = new Date();
  a.setHours(0, 0, 0, 0);
  b.setHours(0, 0, 0, 0);
  return Math.round((a.getTime() - b.getTime()) / 86400000);
}

function dayWord(iso: string): string {
  const n = daysFromToday(iso);
  if (n === 0) return "today";
  if (n === 1) return "tomorrow";
  if (n < 7) return `on ${new Date(iso).toLocaleDateString([], { weekday: "long" })}`;
  return `on ${new Date(iso).toLocaleDateString([], { month: "long", day: "numeric" })}`;
}

function dayHeading(iso: string): string {
  const n = daysFromToday(iso);
  const date = new Date(iso).toLocaleDateString([], { weekday: "long", month: "short", day: "numeric" });
  return n === 0 ? `Today · ${date}` : n === 1 ? `Tomorrow · ${date}` : date;
}

function shortDay(iso: string): string {
  const n = daysFromToday(iso);
  return n === 0 ? "Today" : n === 1 ? "Tmrw" : new Date(iso).toLocaleDateString([], { weekday: "short" });
}
