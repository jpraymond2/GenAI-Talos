import type { ConnectionsState, DeviceMode, SessionUser } from "../types";
import { ConnectionPill } from "./Connections";
import { Avatar } from "./ui";

export type Tab = "assistant" | "calendar" | "contacts" | "sync";

interface Props {
  device: DeviceMode;
  onDevice: (d: DeviceMode) => void;
  tab: Tab;
  onTab: (t: Tab) => void;
  user: SessionUser | null;
  onLogout: () => void;
  sampleMode: boolean;
  connections: ConnectionsState | null;
}

const TABS: { id: Tab; label: string }[] = [
  { id: "assistant", label: "Assistant" },
  { id: "calendar", label: "Calendar" },
  { id: "contacts", label: "Contacts" },
  { id: "sync", label: "Connections" },
];

export function TopBar({ device, onDevice, tab, onTab, user, onLogout, sampleMode, connections }: Props) {
  return (
    <header className="sticky top-0 z-30 border-b border-ink-200/70 bg-white/80 backdrop-blur">
      <div className="mx-auto flex max-w-[1400px] flex-wrap items-center gap-3 px-3 py-2.5 md:px-6">
        <p className="font-display text-2xl font-semibold tracking-tight text-ink-950">Recall</p>
        {sampleMode ? (
          <span className="rounded-md bg-sea-500/15 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-sea-600">Demo data</span>
        ) : null}

        <nav className="order-3 flex w-full gap-1 md:order-none md:ml-6 md:w-auto" aria-label="Sections">
          {TABS.map((t) => (
            <button
              key={t.id}
              type="button"
              onClick={() => onTab(t.id)}
              className={`flex-1 whitespace-nowrap rounded-lg px-2 py-1.5 text-[13px] sm:px-2.5 sm:text-sm font-semibold transition md:flex-none ${
                tab === t.id ? "bg-ink-900 text-white" : "text-ink-500 hover:bg-ink-100 hover:text-ink-900"
              }`}
            >
              {t.label}
            </button>
          ))}
        </nav>

        <div className="ml-auto flex items-center gap-3">
          <ConnectionPill state={connections} onClick={() => onTab("sync")} />
          <div className="flex rounded-xl bg-ink-100 p-1" role="radiogroup" aria-label="Device">
            {(["mac", "phone"] as DeviceMode[]).map((d) => (
              <button
                key={d}
                type="button"
                role="radio"
                aria-checked={device === d}
                onClick={() => onDevice(d)}
                className={`flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold transition ${
                  device === d ? "bg-white text-ink-950 shadow-sm" : "text-ink-500 hover:text-ink-800"
                }`}
              >
                {d === "mac" ? <LaptopIcon /> : <PhoneIcon />}
                {d === "mac" ? "Mac" : "Phone"}
              </button>
            ))}
          </div>
          {user ? (
            <div className="group relative">
              <button type="button" className="flex items-center gap-2 rounded-full" aria-label="Account">
                <Avatar name={user.name || user.email || "You"} size="sm" />
              </button>
              <div className="invisible absolute right-0 top-10 w-52 rounded-xl border border-ink-200 bg-white p-2 opacity-0 shadow-panel transition group-focus-within:visible group-focus-within:opacity-100 group-hover:visible group-hover:opacity-100">
                <p className="truncate px-2 pt-1 text-sm font-semibold text-ink-900">{user.name}</p>
                {user.email ? <p className="truncate px-2 text-xs text-ink-400">{user.email}</p> : null}
                <button type="button" onClick={onLogout} className="mt-2 w-full rounded-lg px-2 py-1.5 text-left text-sm text-ink-700 hover:bg-ink-100">
                  Sign out
                </button>
              </div>
            </div>
          ) : null}
        </div>
      </div>
    </header>
  );
}

function LaptopIcon() {
  return (
    <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
      <rect x="4" y="5" width="16" height="11" rx="1.5" />
      <path d="M2 19h20" strokeLinecap="round" />
    </svg>
  );
}

function PhoneIcon() {
  return (
    <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
      <rect x="7" y="2.5" width="10" height="19" rx="2" />
      <path d="M11 18h2" strokeLinecap="round" />
    </svg>
  );
}
