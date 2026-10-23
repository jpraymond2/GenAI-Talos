import type { Evidence, EvidenceSource } from "../types";

const LABELS: Record<EvidenceSource, string> = {
  gmail: "Gmail",
  whatsapp: "WhatsApp",
  imessage: "iMessage",
  calendar: "Calendar",
  web: "Web",
  memory: "Memory",
  linkedin: "LinkedIn",
};

const TONES: Record<EvidenceSource, string> = {
  gmail: "bg-rose-50 text-rose-800 border-rose-200",
  whatsapp: "bg-emerald-50 text-emerald-800 border-emerald-200",
  imessage: "bg-sky-50 text-sky-800 border-sky-200",
  calendar: "bg-amber-50 text-amber-900 border-amber-200",
  web: "bg-slate-50 text-slate-700 border-slate-200",
  memory: "bg-teal-50 text-teal-900 border-teal-200",
  linkedin: "bg-blue-50 text-blue-900 border-blue-200",
};

function Icon({ source }: { source: EvidenceSource }) {
  const common = "h-3.5 w-3.5";
  switch (source) {
    case "gmail":
      return (
        <svg className={common} viewBox="0 0 24 24" fill="currentColor" aria-hidden>
          <path d="M20 4H4a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V6a2 2 0 0 0-2-2Zm0 4-8 5L4 8V6l8 5 8-5v2Z" />
        </svg>
      );
    case "whatsapp":
      return (
        <svg className={common} viewBox="0 0 24 24" fill="currentColor" aria-hidden>
          <path d="M12 2a10 10 0 0 0-8.7 14.9L2 22l5.3-1.4A10 10 0 1 0 12 2Zm0 2a8 8 0 0 1 6.8 12.2l-.3.5.5 1.9-1.9-.5-.5.3A8 8 0 1 1 12 4Zm4.1 9.4c-.2-.1-1.3-.6-1.5-.7-.2-.1-.4-.1-.5.1l-.7.8c-.1.1-.3.2-.5.1a6.5 6.5 0 0 1-3.2-2.8c-.1-.2 0-.4.1-.5l.6-.7c.1-.1.1-.3 0-.5l-.7-1.6c-.2-.4-.4-.4-.5-.4h-.5c-.2 0-.5.1-.7.3-.2.3-.9.9-.9 2.1s.9 2.4 1 2.6c.1.2 1.8 2.8 4.4 3.8 2.6 1 2.6.7 3.1.7.5 0 1.5-.6 1.7-1.2.2-.6.2-1.1.1-1.2-.1-.1-.3-.2-.5-.3Z" />
        </svg>
      );
    case "imessage":
      return (
        <svg className={common} viewBox="0 0 24 24" fill="currentColor" aria-hidden>
          <path d="M12 3C6.5 3 2 6.6 2 11c0 2.4 1.3 4.5 3.4 6l-.6 3.5 3.7-1.5c1 .3 2.2.5 3.5.5 5.5 0 10-3.6 10-8S17.5 3 12 3Z" />
        </svg>
      );
    case "calendar":
      return (
        <svg className={common} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
          <rect x="3" y="5" width="18" height="16" rx="2" />
          <path d="M3 10h18M8 3v4M16 3v4" />
        </svg>
      );
    case "web":
      return (
        <svg className={common} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
          <circle cx="12" cy="12" r="9" />
          <path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18" />
        </svg>
      );
    case "memory":
      return (
        <svg className={common} viewBox="0 0 24 24" fill="currentColor" aria-hidden>
          <path d="M9 3h6v3h4v4h-2v8a3 3 0 0 1-3 3H10a3 3 0 0 1-3-3v-8H5V6h4V3Zm2 2v1h2V5h-2Zm-3 5v8a1 1 0 0 0 1 1h6a1 1 0 0 0 1-1v-8H8Z" />
        </svg>
      );
    default:
      return (
        <svg className={common} viewBox="0 0 24 24" fill="currentColor" aria-hidden>
          <circle cx="12" cy="12" r="8" />
        </svg>
      );
  }
}

interface Props {
  evidence: Evidence;
  expanded: boolean;
  onToggle: () => void;
}

export function SourceChip({ evidence, expanded, onToggle }: Props) {
  return (
    <div className="inline-flex flex-col items-start gap-1">
      <button
        type="button"
        onClick={onToggle}
        className={`inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-xs font-medium transition hover:brightness-95 ${TONES[evidence.source]}`}
        aria-expanded={expanded}
      >
        <Icon source={evidence.source} />
        {LABELS[evidence.source]}
      </button>
      {expanded ? (
        <div className="animate-rise max-w-sm rounded-lg border border-ink-200 bg-white/90 p-3 text-xs text-ink-700 shadow-panel">
          <p className="leading-relaxed">{evidence.text}</p>
          <p className="mt-2 font-mono text-[11px] text-ink-400">
            {new Date(evidence.ts).toLocaleString()} · {evidence.source_ref}
          </p>
        </div>
      ) : null}
    </div>
  );
}
