import { useEffect, useMemo, useRef, useState } from "react";
import { fetchDevice, fetchMacContacts, latestPull, startPull } from "../workspaceApi";
import type { ConnectionsState, DeviceMode, DeviceStatus, MacContact, PullJob, PullParams, SourceId } from "../types";
import { ConnectionsPanel } from "./Connections";
import { Avatar, Spinner, timeAgo } from "./ui";

interface Props {
  device: DeviceMode;
  onSwitchDevice: (d: DeviceMode) => void;
  onViewContacts: () => void;
  connections: ConnectionsState | null;
  refreshConnections: () => Promise<void>;
}

type Focus = { source: SourceId; n: number } | null;

const RANGES = [
  { days: 7, label: "7 days" },
  { days: 30, label: "30 days" },
  { days: 90, label: "90 days" },
  { days: 180, label: "6 months" },
  { days: 365, label: "1 year" },
];

const PRESETS: { id: string; title: string; blurb: string; params: Partial<PullParams> }[] = [
  { id: "quick", title: "Quick catch-up", blurb: "Last 30 days · iMessage + address book · everyone",
    params: { days: 30, sources: ["imessage", "contacts"], contacts: "all", include_groups: true } },
  { id: "deep", title: "Full history", blurb: "Last year · iMessage, WhatsApp + address book",
    params: { days: 365, sources: ["imessage", "whatsapp", "contacts"], contacts: "all", include_groups: true } },
  { id: "people", title: "Just a few people", blurb: "Last 90 days · only the contacts you pick",
    params: { days: 90, sources: ["imessage", "whatsapp", "contacts"], contacts: "selected", include_groups: false } },
];

const DEFAULT: PullParams = {
  days: 30, sources: ["imessage", "contacts"], contacts: "all", contact_ids: [], contact_handles: [], include_groups: true, max_group_size: 15,
};

export function SyncPage({ device, onSwitchDevice, onViewContacts, connections, refreshConnections }: Props) {
  const [focus, setFocus] = useState<Focus>(null);
  return (
    <div className="space-y-5">
      <div className="mx-auto max-w-[1100px]">
        <ConnectionsPanel
          state={connections}
          refresh={refreshConnections}
          onPull={(source) => {
            if (device === "phone") onSwitchDevice("mac");
            setFocus({ source, n: Date.now() });
          }}
        />
      </div>
      {device === "phone" ? (
        <PhoneSync onSwitchDevice={onSwitchDevice} />
      ) : (
        <MacSync onViewContacts={onViewContacts} focus={focus} onPulled={refreshConnections} />
      )}
    </div>
  );
}

function MacSync({ onViewContacts, focus, onPulled }: { onViewContacts: () => void; focus: Focus; onPulled: () => Promise<void> }) {
  const [status, setStatus] = useState<DeviceStatus | null>(null);
  const [contacts, setContacts] = useState<MacContact[]>([]);
  const [params, setParams] = useState<PullParams>(DEFAULT);
  const [preset, setPreset] = useState<string>("quick");
  const [query, setQuery] = useState("");
  const [pull, setPull] = useState<PullJob | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const poll = useRef<number | null>(null);
  const formRef = useRef<HTMLElement | null>(null);

  // A "Pull" click on a connection tile: make sure that source is ticked, then jump to the form.
  useEffect(() => {
    if (!focus) return;
    if (focus.source === "imessage" || focus.source === "whatsapp" || focus.source === "contacts") {
      const src = focus.source;
      setPreset("custom");
      setParams((cur) => (cur.sources.includes(src) ? cur : { ...cur, sources: [...cur.sources, src] }));
    }
    formRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [focus]);

  useEffect(() => {
    if (pull?.status === "ready") void onPulled();
  }, [pull?.status, onPulled]);

  useEffect(() => {
    void fetchDevice().then(setStatus).catch(() => setStatus(null));
    void fetchMacContacts().then(setContacts).catch(() => setContacts([]));
    void latestPull().then((r) => { setPull(r.pull); setStatus(r.device); }).catch(() => undefined);
    const id = window.setInterval(() => void fetchDevice().then(setStatus).catch(() => undefined), 10000);
    return () => window.clearInterval(id);
  }, []);

  const active = pull && ["queued", "running", "uploading", "building"].includes(pull.status);
  useEffect(() => {
    if (!active) return;
    poll.current = window.setInterval(async () => {
      try {
        const r = await latestPull();
        setPull(r.pull);
        setStatus(r.device);
      } catch {
        /* keep polling */
      }
    }, 1200);
    return () => {
      if (poll.current) window.clearInterval(poll.current);
    };
  }, [active]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    const list = q ? contacts.filter((c) => `${c.name} ${c.organization} ${c.phones.join(" ")} ${c.emails.join(" ")}`.toLowerCase().includes(q)) : contacts;
    return list.slice(0, 300);
  }, [contacts, query]);

  function applyPreset(id: string) {
    const p = PRESETS.find((x) => x.id === id);
    setPreset(id);
    if (p) setParams((cur) => ({ ...cur, ...p.params, contact_ids: p.params.contacts === "selected" ? cur.contact_ids : [] }));
  }

  function toggleSource(s: PullParams["sources"][number]) {
    setPreset("custom");
    setParams((cur) => ({ ...cur, sources: cur.sources.includes(s) ? cur.sources.filter((x) => x !== s) : [...cur.sources, s] }));
  }

  function toggleContact(id: string) {
    setParams((cur) => ({ ...cur, contact_ids: cur.contact_ids.includes(id) ? cur.contact_ids.filter((x) => x !== id) : [...cur.contact_ids, id] }));
  }

  async function go() {
    setError(null);
    setStarting(true);
    try {
      setPull(await startPull(params));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't start the pull");
    } finally {
      setStarting(false);
    }
  }

  const noSource = params.sources.length === 0;
  const noPick = params.contacts === "selected" && params.contact_ids.length === 0;
  const rangeLabel = RANGES.find((r) => r.days === params.days)?.label ?? `${params.days} days`;
  const summary = `Pull the last ${rangeLabel} of ${listify(params.sources.map(sourceName))}${
    params.contacts === "all" ? " for everyone" : ` for ${params.contact_ids.length} chosen ${params.contact_ids.length === 1 ? "person" : "people"}`
  }${params.include_groups ? `, including group chats up to ${params.max_group_size} people` : ", one-on-one chats only"}.`;

  return (
    <div className="mx-auto grid max-w-[1100px] gap-5 lg:grid-cols-[1fr_340px]">
      <div className="space-y-5">
        <section className="rounded-2xl border border-ink-200/80 bg-white/80 p-5 shadow-panel">
          <div className="flex items-center gap-3">
            <span className={`h-2.5 w-2.5 rounded-full ${status?.online ? "bg-sea-500" : status?.connected ? "bg-ember-400" : "bg-ink-300"}`} />
            <div className="min-w-0">
              <p className="font-semibold text-ink-900">
                {status?.online ? `${status.hostname || "Your Mac"} is connected` : status?.connected ? `${status.hostname || "Your Mac"} is offline` : "Connect your Mac"}
              </p>
              <p className="text-xs text-ink-500">
                {status?.connected ? `Last check-in ${timeAgo(status.last_seen)}` : "Recall reads Messages and Contacts on your Mac — nothing leaves it until you press Pull."}
              </p>
            </div>
          </div>
          {!status?.online ? <SetupSteps /> : null}
        </section>

        <section ref={formRef} className="scroll-mt-24 rounded-2xl border border-ink-200/80 bg-white/80 p-5 shadow-panel">
          <h2 className="font-display text-xl font-semibold text-ink-950">What to pull</h2>
          <div className="mt-4 grid gap-3 sm:grid-cols-3">
            {PRESETS.map((p) => (
              <button
                key={p.id}
                type="button"
                onClick={() => applyPreset(p.id)}
                className={`rounded-xl border px-4 py-3 text-left transition ${
                  preset === p.id ? "border-sea-500 bg-sea-500/5 ring-2 ring-sea-500/20" : "border-ink-200 hover:border-ink-300"
                }`}
              >
                <p className="text-sm font-semibold text-ink-900">{p.title}</p>
                <p className="mt-1 text-xs leading-snug text-ink-500">{p.blurb}</p>
              </button>
            ))}
          </div>

          <Label>Since</Label>
          <div className="flex flex-wrap gap-2">
            {RANGES.map((r) => (
              <Pill key={r.days} on={params.days === r.days} onClick={() => { setPreset("custom"); setParams({ ...params, days: r.days }); }}>
                Past {r.label}
              </Pill>
            ))}
          </div>

          <Label>From</Label>
          <div className="flex flex-wrap gap-2">
            {(["imessage", "whatsapp", "contacts"] as const).map((s) => (
              <Pill key={s} on={params.sources.includes(s)} onClick={() => toggleSource(s)}>
                {params.sources.includes(s) ? "✓ " : ""}{sourceName(s)}
              </Pill>
            ))}
          </div>

          <Label>Who</Label>
          <div className="flex flex-wrap gap-2">
            <Pill on={params.contacts === "all"} onClick={() => { setPreset("custom"); setParams({ ...params, contacts: "all" }); }}>Everyone</Pill>
            <Pill on={params.contacts === "selected"} onClick={() => { setPreset("custom"); setParams({ ...params, contacts: "selected" }); }}>
              Choose contacts{params.contact_ids.length ? ` (${params.contact_ids.length})` : ""}
            </Pill>
          </div>
          {params.contacts === "selected" ? (
            <div className="mt-3 rounded-xl border border-ink-200 bg-ink-50/60 p-3">
              <div className="flex gap-2">
                <input
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder={contacts.length ? `Search ${contacts.length} contacts…` : "Contacts appear once your Mac connects"}
                  className="min-w-0 flex-1 rounded-lg border border-ink-200 bg-white px-3 py-2 text-sm outline-none ring-sea-500/40 focus:ring-2"
                />
                <button type="button" onClick={() => setParams({ ...params, contact_ids: [] })} className="rounded-lg px-3 text-xs font-semibold text-ink-500 hover:bg-ink-100">
                  Clear
                </button>
              </div>
              <ul className="scroll-thin mt-2 max-h-64 space-y-1 overflow-y-auto">
                {filtered.map((c) => {
                  const on = params.contact_ids.includes(c.contact_id);
                  return (
                    <li key={c.contact_id}>
                      <button
                        type="button"
                        onClick={() => toggleContact(c.contact_id)}
                        className={`flex w-full items-center gap-3 rounded-lg px-2 py-1.5 text-left transition ${on ? "bg-sea-500/10" : "hover:bg-white"}`}
                      >
                        <span className={`flex h-4 w-4 items-center justify-center rounded border text-[10px] ${on ? "border-sea-500 bg-sea-500 text-white" : "border-ink-300 bg-white"}`}>
                          {on ? "✓" : ""}
                        </span>
                        <Avatar name={c.name} size="sm" />
                        <span className="min-w-0">
                          <span className="block truncate text-sm font-medium text-ink-900">{c.name}</span>
                          <span className="block truncate text-xs text-ink-400">{c.organization || c.phones[0] || c.emails[0] || ""}</span>
                        </span>
                      </button>
                    </li>
                  );
                })}
                {filtered.length === 0 ? <li className="px-2 py-3 text-sm text-ink-400">No contacts match.</li> : null}
              </ul>
            </div>
          ) : null}

          <Label>Group chats</Label>
          <div className="flex flex-wrap items-center gap-3">
            <Pill on={params.include_groups} onClick={() => setParams({ ...params, include_groups: !params.include_groups })}>
              {params.include_groups ? "✓ Include group chats" : "One-on-one only"}
            </Pill>
            {params.include_groups ? (
              <label className="flex items-center gap-2 text-xs text-ink-500">
                up to
                <input
                  type="range" min={3} max={50} value={params.max_group_size}
                  onChange={(e) => setParams({ ...params, max_group_size: Number(e.target.value) })}
                  className="accent-[#2a7f74]"
                />
                <span className="w-16 font-mono text-ink-700">{params.max_group_size} people</span>
              </label>
            ) : null}
          </div>
        </section>
      </div>

      <aside className="space-y-4 lg:sticky lg:top-20 lg:self-start">
        <section className="rounded-2xl border border-ink-200/80 bg-white/90 p-5 shadow-panel">
          <p className="text-[11px] font-semibold uppercase tracking-wider text-ink-500">Summary</p>
          <p className="mt-2 text-sm leading-relaxed text-ink-800">{noSource ? "Pick at least one source." : summary}</p>
          {error ? <p className="mt-3 rounded-lg bg-ember-400/10 px-3 py-2 text-sm text-ember-600">{error}</p> : null}
          <button
            type="button"
            onClick={() => void go()}
            disabled={starting || Boolean(active) || noSource || noPick}
            className="mt-4 flex w-full items-center justify-center gap-2 rounded-xl bg-sea-600 px-4 py-3 text-sm font-semibold text-white shadow-sm transition hover:bg-sea-500 disabled:opacity-40"
          >
            {starting || active ? <Spinner /> : <DownloadIcon />}
            {active ? "Pulling…" : "Pull messages"}
          </button>
          {!status?.online && !active ? (
            <p className="mt-2 text-center text-xs text-ink-400">Your Mac is offline — the pull starts as soon as it checks in.</p>
          ) : null}
        </section>
        {pull ? <PullProgress pull={pull} onViewContacts={onViewContacts} /> : null}
      </aside>
    </div>
  );
}

const STAGES: { key: PullJob["status"]; label: string }[] = [
  { key: "queued", label: "Waiting for your Mac" },
  { key: "running", label: "Reading messages on your Mac" },
  { key: "uploading", label: "Uploading securely" },
  { key: "building", label: "Sorting people into contacts" },
  { key: "ready", label: "Contacts ready" },
];

function PullProgress({ pull, onViewContacts }: { pull: PullJob; onViewContacts: () => void }) {
  const idx = STAGES.findIndex((s) => s.key === pull.status);
  const p = pull.progress as Record<string, number | string>;
  const msgs = Number(p.imessage_messages ?? 0) + Number(p.whatsapp_messages ?? 0);
  return (
    <section className="animate-rise rounded-2xl border border-ink-200/80 bg-white/90 p-5 shadow-panel">
      <p className="text-[11px] font-semibold uppercase tracking-wider text-ink-500">Latest pull · {timeAgo(pull.created_at)}</p>
      {pull.status === "failed" || pull.status === "cancelled" ? (
        <p className="mt-3 rounded-lg bg-ember-400/10 px-3 py-2 text-sm text-ember-600">
          {pull.status === "cancelled" ? "Replaced by a newer pull." : `Pull failed: ${pull.error || "unknown error"}`}
        </p>
      ) : (
        <ol className="mt-3 space-y-2.5">
          {STAGES.map((s, i) => (
            <li key={s.key} className="flex items-center gap-3 text-sm">
              <span
                className={`flex h-5 w-5 items-center justify-center rounded-full text-[10px] font-bold ${
                  i < idx || pull.status === "ready" ? "bg-sea-500 text-white" : i === idx ? "bg-ink-900 text-white" : "bg-ink-100 text-ink-400"
                }`}
              >
                {i < idx || pull.status === "ready" ? "✓" : i === idx ? <Spinner className="h-3 w-3" /> : i + 1}
              </span>
              <span className={i <= idx ? "text-ink-900" : "text-ink-400"}>{s.label}</span>
            </li>
          ))}
        </ol>
      )}
      {msgs || p.contacts ? (
        <p className="mt-3 text-xs text-ink-500">
          {msgs ? `${msgs.toLocaleString()} messages` : ""}
          {msgs && p.contacts ? " · " : ""}
          {p.contacts ? `${Number(p.contacts).toLocaleString()} address-book cards` : ""}
        </p>
      ) : null}
      {pull.status === "ready" ? (
        <button type="button" onClick={onViewContacts} className="mt-4 w-full rounded-xl bg-ink-900 px-4 py-2.5 text-sm font-semibold text-white hover:bg-ink-800">
          View contacts →
        </button>
      ) : null}
    </section>
  );
}

function SetupSteps() {
  const origin = typeof window !== "undefined" ? window.location.origin : "https://your-recall-server";
  const steps = [
    { title: "Open Terminal on your Mac, in the Recall folder", cmd: "" },
    { title: "Link this Mac to your account", cmd: `uv run python -m recall.mac_agent init-config --token <DEVICE_UPLOAD_TOKEN> --server-url ${origin}` },
    { title: "Start the agent (allow Full Disk Access when asked)", cmd: "uv run python -m recall.mac_agent watch" },
  ];
  return (
    <ol className="mt-4 space-y-3">
      {steps.map((s, i) => (
        <li key={s.title} className="flex gap-3">
          <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-ink-100 text-xs font-bold text-ink-600">{i + 1}</span>
          <div className="min-w-0 flex-1">
            <p className="text-sm text-ink-800">{s.title}</p>
            {s.cmd ? <CopyLine text={s.cmd} /> : null}
          </div>
        </li>
      ))}
    </ol>
  );
}

function CopyLine({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="mt-1.5 flex items-center gap-2 rounded-lg bg-ink-950 px-3 py-2">
      <code className="scroll-thin min-w-0 flex-1 overflow-x-auto whitespace-nowrap font-mono text-xs text-ink-100">{text}</code>
      <button
        type="button"
        onClick={() => {
          void navigator.clipboard?.writeText(text);
          setCopied(true);
          window.setTimeout(() => setCopied(false), 1500);
        }}
        className="shrink-0 rounded-md bg-white/10 px-2 py-1 text-[11px] font-semibold text-white hover:bg-white/20"
      >
        {copied ? "Copied" : "Copy"}
      </button>
    </div>
  );
}

function PhoneSync({ onSwitchDevice }: { onSwitchDevice: (d: DeviceMode) => void }) {
  return (
    <div className="mx-auto max-w-2xl space-y-4">
      <section className="rounded-2xl border border-ink-200/80 bg-white/80 p-6 shadow-panel">
        <h2 className="font-display text-2xl font-semibold text-ink-950">Using Recall on your phone</h2>
        <p className="mt-2 text-sm leading-relaxed text-ink-600">
          iPhones don't let apps read your Messages, so your phone talks to Recall and your Mac does the syncing.
          iMessages from your iPhone already reach your Mac through iCloud.
        </p>
        <div className="mt-5 grid gap-3 sm:grid-cols-2">
          <div className="rounded-xl border border-ink-200 p-4">
            <p className="text-sm font-semibold text-ink-900">1 · Sync once from your Mac</p>
            <p className="mt-1 text-xs text-ink-500">Pick what to pull — last 30 days, chosen people, with or without groups.</p>
            <button type="button" onClick={() => onSwitchDevice("mac")} className="mt-3 rounded-lg bg-ink-900 px-3 py-2 text-xs font-semibold text-white hover:bg-ink-800">
              Set up on Mac →
            </button>
          </div>
          <div className="rounded-xl border border-ink-200 p-4">
            <p className="text-sm font-semibold text-ink-900">2 · Talk to Recall anywhere</p>
            <p className="mt-1 text-xs text-ink-500">Add this page to your Home Screen, or say “Hey Siri, Recall” with the Recall shortcut.</p>
            <p className="mt-3 text-xs text-ink-400">Safari → Share → Add to Home Screen</p>
          </div>
        </div>
      </section>
    </div>
  );
}

function Label({ children }: { children: React.ReactNode }) {
  return <p className="mb-2 mt-5 text-[11px] font-semibold uppercase tracking-wider text-ink-500">{children}</p>;
}

function Pill({ on, onClick, children }: { on: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={on}
      className={`rounded-full border px-3.5 py-1.5 text-sm font-medium transition ${
        on ? "border-ink-900 bg-ink-900 text-white" : "border-ink-200 bg-white text-ink-700 hover:border-ink-400"
      }`}
    >
      {children}
    </button>
  );
}

function sourceName(s: string): string {
  return s === "imessage" ? "iMessage" : s === "whatsapp" ? "WhatsApp" : "address book";
}

function listify(items: string[]): string {
  if (items.length <= 1) return items.join("");
  return `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;
}

function DownloadIcon() {
  return (
    <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" aria-hidden>
      <path d="M12 4v11m0 0 4.5-4.5M12 15l-4.5-4.5M5 20h14" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
