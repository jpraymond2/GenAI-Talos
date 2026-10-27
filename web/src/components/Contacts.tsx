import { useEffect, useMemo, useState } from "react";
import { editPerson, fetchPeople, fetchPersonCard, mergePeople } from "../workspaceApi";
import type { PersonCard, PersonEdit } from "../types";
import { Avatar, Chip, Field, SourceBadge, Spinner, timeAgo } from "./ui";

type Sort = "recent" | "most" | "name";

/** Scrollable grid of contact cards with search, filters, and an editor drawer. */
export function ContactsPage({ onGoSync, focusId }: { onGoSync: () => void; focusId?: string | null }) {
  const [people, setPeople] = useState<PersonCard[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [source, setSource] = useState<string>("all");
  const [sort, setSort] = useState<Sort>("recent");
  const [open, setOpen] = useState<string | null>(focusId ?? null);

  async function load() {
    try {
      setPeople(await fetchPeople());
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't load contacts");
      setPeople([]);
    }
  }
  useEffect(() => {
    void load();
  }, []);
  useEffect(() => {
    if (focusId) setOpen(focusId);
  }, [focusId]);

  const sources = useMemo(() => Array.from(new Set((people ?? []).flatMap((p) => p.sources))).sort(), [people]);
  const shown = useMemo(() => {
    const needle = q.trim().toLowerCase();
    let list = (people ?? []).filter((p) => source === "all" || p.sources.includes(source));
    if (needle) {
      list = list.filter((p) =>
        [p.name, p.org, p.title, p.city, ...p.other_names, ...p.emails, ...p.phones].join(" ").toLowerCase().includes(needle));
    }
    return [...list].sort((a, b) =>
      sort === "name" ? a.name.localeCompare(b.name) : sort === "most" ? b.mentions - a.mentions : (b.last_seen_at || "").localeCompare(a.last_seen_at || ""));
  }, [people, q, source, sort]);

  if (people === null) {
    return <div className="flex justify-center py-24 text-ink-400"><Spinner className="h-6 w-6" /></div>;
  }

  if (people.length === 0) {
    return (
      <div className="mx-auto max-w-lg rounded-2xl border border-dashed border-ink-300 bg-white/50 p-10 text-center shadow-panel">
        <p className="font-display text-2xl font-semibold text-ink-900">No contacts yet</p>
        <p className="mt-2 text-sm text-ink-500">{error ?? "Pull your messages and Recall will sort everyone you talk to into contact cards."}</p>
        <button type="button" onClick={onGoSync} className="mt-5 rounded-xl bg-sea-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-sea-500">
          Pull messages →
        </button>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-[1400px]">
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <h1 className="mr-3 font-display text-2xl font-semibold text-ink-950">
          Contacts <span className="text-base font-normal text-ink-400">{people.length}</span>
        </h1>
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search name, company, email, phone…"
          className="min-w-0 flex-1 rounded-xl border border-ink-200 bg-white px-3 py-2 text-sm outline-none ring-sea-500/40 focus:ring-2 sm:max-w-sm"
        />
        <select value={source} onChange={(e) => setSource(e.target.value)} className="rounded-xl border border-ink-200 bg-white px-3 py-2 text-sm text-ink-700">
          <option value="all">All sources</option>
          {sources.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        <select value={sort} onChange={(e) => setSort(e.target.value as Sort)} className="rounded-xl border border-ink-200 bg-white px-3 py-2 text-sm text-ink-700">
          <option value="recent">Recently talked</option>
          <option value="most">Most messages</option>
          <option value="name">Name A–Z</option>
        </select>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
        {shown.map((p) => <ContactCard key={p.entity_id} person={p} onOpen={() => setOpen(p.entity_id)} />)}
      </div>
      {shown.length === 0 ? <p className="py-10 text-center text-sm text-ink-400">Nobody matches those filters.</p> : null}

      {open ? (
        <ContactEditor
          entityId={open}
          all={people}
          onClose={() => setOpen(null)}
          onSaved={(card) => setPeople((cur) => (cur ?? []).map((p) => (p.entity_id === card.entity_id ? card : p)))}
          onMerged={() => void load()}
        />
      ) : null}
    </div>
  );
}

export function ContactCard({ person, onOpen, highlight }: { person: PersonCard; onOpen: () => void; highlight?: boolean }) {
  const subtitle = [person.title, person.org].filter(Boolean).join(" · ") || person.city || person.relationship || " ";
  return (
    <button
      type="button"
      onClick={onOpen}
      className={`group flex h-full flex-col rounded-2xl border bg-white/90 p-4 text-left shadow-panel transition hover:-translate-y-0.5 hover:border-sea-500/60 ${
        highlight ? "border-sea-500 ring-2 ring-sea-500/25" : "border-ink-200/80"
      }`}
    >
      <div className="flex items-start gap-3">
        <Avatar name={person.name} />
        <div className="min-w-0 flex-1">
          <p className="truncate font-semibold text-ink-950">{person.name}</p>
          <p className="truncate text-xs text-ink-500">{subtitle}</p>
        </div>
        {person.edited_fields.length ? <span className="rounded bg-sea-500/10 px-1.5 py-0.5 text-[10px] font-semibold text-sea-600">edited</span> : null}
      </div>
      <div className="mt-3 flex flex-wrap gap-1">
        {person.sources.slice(0, 5).map((s) => <SourceBadge key={s} source={s} />)}
      </div>
      <div className="mt-auto flex items-center justify-between pt-3 text-[11px] text-ink-400">
        <span>{person.mentions} messages</span>
        <span>{timeAgo(person.last_seen_at)}</span>
      </div>
    </button>
  );
}

/** Slide-over editor: change the card, add/remove names & handles, merge with another contact. */
export function ContactEditor({
  entityId, all, onClose, onSaved, onMerged,
}: {
  entityId: string;
  all?: PersonCard[];
  onClose: () => void;
  onSaved?: (card: PersonCard) => void;
  onMerged?: () => void;
}) {
  const [card, setCard] = useState<PersonCard | null>(null);
  const [draft, setDraft] = useState<PersonEdit>({});
  const [newAlias, setNewAlias] = useState("");
  const [removed, setRemoved] = useState<string[]>([]);
  const [added, setAdded] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [mergeQ, setMergeQ] = useState("");

  useEffect(() => {
    setCard(null);
    setDraft({});
    setAdded([]);
    setRemoved([]);
    void fetchPersonCard(entityId).then(setCard);
  }, [entityId]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const value = (k: "name" | "org" | "title" | "city" | "notes") => (draft[k] ?? (card ? card[k] : "")) as string;
  const dirty = Object.keys(draft).length > 0 || added.length > 0 || removed.length > 0;
  const mergeOptions = useMemo(() => {
    const needle = mergeQ.trim().toLowerCase();
    if (!needle || !all) return [];
    return all.filter((p) => p.entity_id !== entityId && `${p.name} ${p.org} ${p.emails.join(" ")} ${p.phones.join(" ")}`.toLowerCase().includes(needle)).slice(0, 5);
  }, [mergeQ, all, entityId]);

  async function save() {
    if (!card) return;
    setSaving(true);
    setMsg(null);
    try {
      const next = await editPerson(card.entity_id, { ...draft, add_aliases: added, remove_aliases: removed });
      setCard(next);
      setDraft({});
      setAdded([]);
      setRemoved([]);
      setMsg("Saved — Recall will keep this even after the next sync.");
      onSaved?.(next);
    } catch (err) {
      setMsg(err instanceof Error ? err.message : "Save failed");
    } finally {
      setSaving(false);
    }
  }

  async function merge(otherId: string) {
    if (!card) return;
    setSaving(true);
    try {
      const next = await mergePeople(card.entity_id, otherId, true);
      if (next) setCard(next);
      setMergeQ("");
      setMsg("Merged into one contact.");
      onMerged?.();
    } finally {
      setSaving(false);
    }
  }

  const handles = card ? [...card.emails, ...card.phones, ...card.other_names] : [];

  return (
    <div className="fixed inset-0 z-50 flex justify-end" role="dialog" aria-modal="true">
      <button type="button" className="absolute inset-0 bg-ink-950/30 backdrop-blur-[2px]" onClick={onClose} aria-label="Close" />
      <aside className="animate-rise relative flex h-full w-full max-w-md flex-col bg-white shadow-2xl">
        {!card ? (
          <div className="flex flex-1 items-center justify-center text-ink-400"><Spinner className="h-6 w-6" /></div>
        ) : (
          <>
            <div className="flex items-start gap-4 border-b border-ink-100 p-5">
              <Avatar name={value("name") || card.name} size="lg" />
              <div className="min-w-0 flex-1">
                <p className="truncate font-display text-2xl font-semibold text-ink-950">{value("name") || card.name}</p>
                <p className="text-xs text-ink-500">
                  {card.mentions} messages · last {timeAgo(card.last_seen_at)}
                  {card.merged_from ? ` · combined from ${card.merged_from + 1} entries` : ""}
                </p>
                <div className="mt-2 flex flex-wrap gap-1">{card.sources.map((s) => <SourceBadge key={s} source={s} />)}</div>
              </div>
              <button type="button" onClick={onClose} className="rounded-lg p-1.5 text-ink-400 hover:bg-ink-100 hover:text-ink-800" aria-label="Close">✕</button>
            </div>

            <div className="scroll-thin flex-1 space-y-4 overflow-y-auto p-5">
              <Field label="Name" value={value("name")} onChange={(v) => setDraft({ ...draft, name: v })} edited={card.edited_fields.includes("canonical_name")} />
              <div className="grid grid-cols-2 gap-3">
                <Field label="Company" value={value("org")} onChange={(v) => setDraft({ ...draft, org: v })} edited={card.edited_fields.includes("org")} />
                <Field label="Title" value={value("title")} onChange={(v) => setDraft({ ...draft, title: v })} edited={card.edited_fields.includes("title")} />
              </div>
              <Field label="City" value={value("city")} onChange={(v) => setDraft({ ...draft, city: v })} edited={card.edited_fields.includes("city")} />
              <Field label="Notes" value={value("notes")} onChange={(v) => setDraft({ ...draft, notes: v })} placeholder="Anything Recall should remember" multiline />

              <div>
                <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-wider text-ink-500">Also known as · emails · numbers</p>
                <div className="flex flex-wrap gap-1.5">
                  {handles.filter((h) => !removed.includes(h)).map((h) => (
                    <Chip key={h} onRemove={() => setRemoved([...removed, h])}>{h}</Chip>
                  ))}
                  {added.map((h) => <Chip key={h} tone="sea" onRemove={() => setAdded(added.filter((x) => x !== h))}>{h}</Chip>)}
                </div>
                <form
                  className="mt-2 flex gap-2"
                  onSubmit={(e) => {
                    e.preventDefault();
                    if (newAlias.trim()) setAdded([...added, newAlias.trim()]);
                    setNewAlias("");
                  }}
                >
                  <input value={newAlias} onChange={(e) => setNewAlias(e.target.value)} placeholder="Add a nickname, email or number"
                    className="min-w-0 flex-1 rounded-lg border border-ink-200 px-3 py-2 text-sm outline-none ring-sea-500/40 focus:ring-2" />
                  <button type="submit" className="rounded-lg bg-ink-100 px-3 text-sm font-semibold text-ink-700 hover:bg-ink-200">Add</button>
                </form>
              </div>

              {all ? (
                <div className="rounded-xl border border-ink-200 bg-ink-50/60 p-3">
                  <p className="text-[11px] font-semibold uppercase tracking-wider text-ink-500">Same person as another contact?</p>
                  <input value={mergeQ} onChange={(e) => setMergeQ(e.target.value)} placeholder="Search to merge…"
                    className="mt-2 w-full rounded-lg border border-ink-200 bg-white px-3 py-2 text-sm outline-none ring-sea-500/40 focus:ring-2" />
                  {mergeOptions.map((p) => (
                    <button key={p.entity_id} type="button" disabled={saving} onClick={() => void merge(p.entity_id)}
                      className="mt-2 flex w-full items-center gap-2 rounded-lg bg-white px-2 py-1.5 text-left text-sm hover:bg-sea-500/5">
                      <Avatar name={p.name} size="sm" />
                      <span className="min-w-0 flex-1 truncate">{p.name} <span className="text-ink-400">{p.org}</span></span>
                      <span className="text-xs font-semibold text-sea-600">Merge</span>
                    </button>
                  ))}
                </div>
              ) : null}
            </div>

            <div className="flex items-center gap-3 border-t border-ink-100 p-4">
              <p className="min-w-0 flex-1 text-xs text-ink-500">{msg ?? (dirty ? "Unsaved changes" : "Edits are kept across syncs.")}</p>
              <button type="button" onClick={onClose} className="rounded-xl px-4 py-2.5 text-sm font-semibold text-ink-600 hover:bg-ink-100">Close</button>
              <button type="button" onClick={() => void save()} disabled={!dirty || saving}
                className="flex items-center gap-2 rounded-xl bg-ink-900 px-4 py-2.5 text-sm font-semibold text-white hover:bg-ink-800 disabled:opacity-40">
                {saving ? <Spinner /> : null}Save
              </button>
            </div>
          </>
        )}
      </aside>
    </div>
  );
}

/** The card for whoever the assistant just identified from your voice — with a one-tap editor. */
export function IdentifiedContact({ entityId, compact }: { entityId: string; compact?: boolean }) {
  const [card, setCard] = useState<PersonCard | null>(null);
  const [editing, setEditing] = useState(false);
  useEffect(() => {
    void fetchPersonCard(entityId).then(setCard);
  }, [entityId]);
  if (!card) return null;
  return (
    <section className={`animate-rise rounded-2xl border border-sea-500/30 bg-white/95 shadow-panel ${compact ? "p-3" : "p-4"}`}>
      <p className="text-[10px] font-semibold uppercase tracking-wider text-sea-600">Identified contact</p>
      <div className="mt-2 flex items-center gap-3">
        <Avatar name={card.name} />
        <div className="min-w-0 flex-1">
          <p className="truncate font-semibold text-ink-950">{card.name}</p>
          <p className="truncate text-xs text-ink-500">{[card.title, card.org, card.city].filter(Boolean).join(" · ") || "No details yet"}</p>
        </div>
        <button type="button" onClick={() => setEditing(true)} className="rounded-lg border border-ink-200 px-3 py-1.5 text-xs font-semibold text-ink-700 hover:border-sea-500 hover:text-sea-600">
          Edit
        </button>
      </div>
      <div className="mt-2 flex flex-wrap gap-1">
        {[...card.phones.slice(0, 1), ...card.emails.slice(0, 2)].map((h) => <Chip key={h}>{h}</Chip>)}
        {card.sources.map((s) => <SourceBadge key={s} source={s} />)}
      </div>
      {editing ? <ContactEditor entityId={entityId} onClose={() => setEditing(false)} onSaved={setCard} /> : null}
    </section>
  );
}
