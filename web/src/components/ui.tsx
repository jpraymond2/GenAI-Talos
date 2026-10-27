/** Small shared pieces: avatar, source badges, chips, field. */

const AVATAR_TONES = [
  "from-sea-400 to-sea-600",
  "from-ember-400 to-ember-600",
  "from-ink-400 to-ink-700",
  "from-[#7c9cbf] to-[#4b6f96]",
  "from-[#b48ac9] to-[#7d5299]",
  "from-[#c98a8a] to-[#9a4f4f]",
];

export function initials(name: string): string {
  const clean = name.replace(/\([^)]*\)/g, "").trim();
  if (clean.startsWith("+")) return "#";
  const parts = clean.split(/\s+/).filter(Boolean);
  return ((parts[0]?.[0] ?? "?") + (parts.length > 1 ? parts[parts.length - 1][0] : "")).toUpperCase();
}

export function Avatar({ name, size = "md" }: { name: string; size?: "sm" | "md" | "lg" }) {
  let hash = 0;
  for (const ch of name) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0;
  const tone = AVATAR_TONES[hash % AVATAR_TONES.length];
  const dims = size === "lg" ? "h-16 w-16 text-xl" : size === "sm" ? "h-8 w-8 text-xs" : "h-11 w-11 text-sm";
  return (
    <div className={`flex shrink-0 items-center justify-center rounded-full bg-gradient-to-br ${tone} ${dims} font-semibold text-white shadow-sm`} aria-hidden>
      {initials(name)}
    </div>
  );
}

const SOURCE_STYLE: Record<string, { label: string; cls: string }> = {
  gmail: { label: "Gmail", cls: "bg-[#fde8e6] text-[#a1352b]" },
  imessage: { label: "iMessage", cls: "bg-[#e3f1ff] text-[#1f5fa3]" },
  whatsapp: { label: "WhatsApp", cls: "bg-[#dff5e6] text-[#1d7a43]" },
  messenger: { label: "Messenger", cls: "bg-[#ece6ff] text-[#5a3fb3]" },
  calendar: { label: "Calendar", cls: "bg-[#fff2d9] text-[#8a5a00]" },
  contact: { label: "Contacts", cls: "bg-ink-100 text-ink-700" },
  contacts: { label: "Contacts", cls: "bg-ink-100 text-ink-700" },
  linkedin: { label: "LinkedIn", cls: "bg-[#e1edf7] text-[#21547d]" },
};

export function SourceBadge({ source }: { source: string }) {
  const s = SOURCE_STYLE[source] ?? { label: source, cls: "bg-ink-100 text-ink-600" };
  return <span className={`rounded-md px-1.5 py-0.5 text-[11px] font-semibold ${s.cls}`}>{s.label}</span>;
}

export function Chip({ children, onRemove, tone = "ink" }: { children: React.ReactNode; onRemove?: () => void; tone?: "ink" | "sea" }) {
  return (
    <span
      className={`inline-flex max-w-full items-center gap-1 rounded-full px-2.5 py-1 text-xs ${
        tone === "sea" ? "bg-sea-500/10 text-sea-600" : "bg-ink-100 text-ink-700"
      }`}
    >
      <span className="truncate">{children}</span>
      {onRemove ? (
        <button type="button" onClick={onRemove} className="ml-0.5 rounded-full px-1 text-ink-400 hover:bg-ink-200 hover:text-ink-800" aria-label="Remove">
          ×
        </button>
      ) : null}
    </span>
  );
}

export function Field({
  label, value, onChange, placeholder, multiline, edited,
}: {
  label: string; value: string; onChange: (v: string) => void; placeholder?: string; multiline?: boolean; edited?: boolean;
}) {
  const cls = "w-full rounded-xl border border-ink-200 bg-white px-3 py-2.5 text-sm text-ink-900 outline-none ring-sea-500/40 transition focus:border-sea-500 focus:ring-2";
  return (
    <label className="block">
      <span className="mb-1 flex items-center gap-2 text-[11px] font-semibold uppercase tracking-wider text-ink-500">
        {label}
        {edited ? <span className="rounded bg-sea-500/10 px-1.5 py-px text-[10px] normal-case tracking-normal text-sea-600">edited by you</span> : null}
      </span>
      {multiline ? (
        <textarea value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder} rows={3} className={cls} />
      ) : (
        <input value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder} className={cls} />
      )}
    </label>
  );
}

export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return "—";
  const ms = Date.now() - new Date(iso).getTime();
  if (Number.isNaN(ms)) return "—";
  const m = Math.round(ms / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h} h ago`;
  const d = Math.round(h / 24);
  if (d < 30) return `${d} d ago`;
  const mo = Math.round(d / 30);
  return mo < 12 ? `${mo} mo ago` : `${Math.round(mo / 12)} y ago`;
}

export function Spinner({ className = "h-4 w-4" }: { className?: string }) {
  return <span className={`inline-block animate-spin rounded-full border-2 border-current border-t-transparent ${className}`} aria-hidden />;
}
