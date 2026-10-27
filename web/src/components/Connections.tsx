import { useCallback, useEffect, useState } from "react";
import { connectGoogle, disconnectGoogle, fetchConnections } from "../workspaceApi";
import type { ConnectionsState, SourceConnection, SourceId } from "../types";
import { Spinner, timeAgo } from "./ui";

/** Shared poller so the top bar and the Connections page agree. */
export function useConnections(intervalMs = 20000) {
  const [state, setState] = useState<ConnectionsState | null>(null);
  const refresh = useCallback(async () => {
    try {
      setState(await fetchConnections());
    } catch {
      /* keep last known */
    }
  }, []);
  useEffect(() => {
    void refresh();
    const id = window.setInterval(() => void refresh(), intervalMs);
    return () => window.clearInterval(id);
  }, [refresh, intervalMs]);
  return { state, refresh };
}

const DOT: Record<SourceConnection["status"], string> = {
  connected: "bg-sea-500",
  ready: "bg-ember-400",
  off: "bg-ink-300",
};

/** Compact "4/5 connected" pill for the top bar. */
export function ConnectionPill({ state, onClick }: { state: ConnectionsState | null; onClick: () => void }) {
  if (!state) return null;
  return (
    <button
      type="button"
      onClick={onClick}
      className="hidden items-center gap-2 rounded-full border border-ink-200 bg-white px-3 py-1.5 text-xs font-semibold text-ink-700 transition hover:border-ink-400 sm:flex"
      title={state.sources.map((s) => `${s.name}: ${s.status === "connected" ? "connected" : s.detail}`).join("\n")}
    >
      <span className="flex -space-x-0.5">
        {state.sources.map((s) => (
          <span key={s.id} className={`h-2 w-2 rounded-full ring-2 ring-white ${DOT[s.status]}`} />
        ))}
      </span>
      {state.connected}/{state.total} connected
    </button>
  );
}

interface PanelProps {
  state: ConnectionsState | null;
  refresh: () => Promise<void>;
  /** Scroll to / open the Mac pull form. */
  onPull: (source: SourceId) => void;
}

/** "What's connected and what isn't" — one tile per source with the next action. */
export function ConnectionsPanel({ state, refresh, onPull }: PanelProps) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function google(action: "connect" | "disconnect") {
    setError(null);
    setBusy(action);
    try {
      if (action === "connect") {
        const r = await connectGoogle();
        if (r === "redirecting") return; // page navigates to Google
      } else {
        await disconnectGoogle();
      }
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't reach Google");
    } finally {
      setBusy(null);
    }
  }

  const mac = state?.sources.filter((s) => s.via === "mac") ?? [];
  const goog = state?.sources.filter((s) => s.via === "google") ?? [];

  return (
    <section className="rounded-2xl border border-ink-200/80 bg-white/80 p-5 shadow-panel">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="font-display text-xl font-semibold text-ink-950">Connections</h2>
        <p className="text-xs text-ink-500">
          {state ? `${state.connected} of ${state.total} sources connected` : "Checking…"}
        </p>
      </div>

      <div className="mt-4 grid gap-4 md:grid-cols-2">
        <Group
          title="From your Mac"
          note={state?.mac.online ? `${state.mac.hostname || "Mac"} online` : state?.mac.connected ? `Mac last seen ${timeAgo(state.mac.last_seen)}` : "Mac not linked yet"}
          online={Boolean(state?.mac.online)}
        >
          {mac.map((s) => (
            <Tile key={s.id} source={s}>
              <button type="button" onClick={() => onPull(s.id)} className={btn(s.status !== "connected")}>
                {s.action === "setup_mac" ? "Set up" : s.status === "connected" ? "Pull again" : "Pull"}
              </button>
            </Tile>
          ))}
        </Group>

        <Group
          title="Google account"
          note={state?.google.connected ? "Signed in" : state?.google.configured === false ? "Sign-in not set up on server" : "Not signed in"}
          online={Boolean(state?.google.connected)}
        >
          {goog.map((s) => (
            <Tile key={s.id} source={s}>
              {s.status === "connected" ? (
                <button type="button" disabled={busy !== null} onClick={() => void google("disconnect")} className={btn(false)}>
                  {busy === "disconnect" ? <Spinner className="h-3 w-3" /> : "Disconnect"}
                </button>
              ) : (
                <button type="button" disabled={busy !== null || state?.google.configured === false} onClick={() => void google("connect")} className={btn(true)}>
                  {busy === "connect" ? <Spinner className="h-3 w-3" /> : <GoogleG />}
                  Connect
                </button>
              )}
            </Tile>
          ))}
          {state?.google.configured === false ? (
            <p className="px-1 text-[11px] leading-snug text-ink-400">
              Add a Web OAuth client as <code className="font-mono">secrets/credentials.json</code> with redirect{" "}
              <code className="break-all font-mono">{window.location.origin}/api/connect/google/callback</code>.
            </p>
          ) : null}
        </Group>
      </div>
      {error ? <p className="mt-3 rounded-lg bg-ember-400/10 px-3 py-2 text-sm text-ember-600">{error}</p> : null}
    </section>
  );
}

function Group({ title, note, online, children }: { title: string; note: string; online: boolean; children: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-ink-100 bg-ink-50/50 p-3">
      <div className="mb-2 flex items-center justify-between px-1">
        <p className="text-[11px] font-semibold uppercase tracking-wider text-ink-500">{title}</p>
        <p className="flex items-center gap-1.5 text-[11px] text-ink-400">
          <span className={`h-1.5 w-1.5 rounded-full ${online ? "bg-sea-500" : "bg-ink-300"}`} />
          {note}
        </p>
      </div>
      <div className="space-y-2">{children}</div>
    </div>
  );
}

function Tile({ source, children }: { source: SourceConnection; children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-3 rounded-lg bg-white px-3 py-2.5 shadow-sm">
      <SourceIcon id={source.id} />
      <div className="min-w-0 flex-1">
        <p className="flex items-center gap-2 text-sm font-semibold text-ink-900">
          {source.name}
          <span className={`rounded px-1.5 py-px text-[10px] font-semibold uppercase tracking-wide ${
            source.status === "connected" ? "bg-sea-500/10 text-sea-600" : source.status === "ready" ? "bg-ember-400/15 text-ember-600" : "bg-ink-100 text-ink-500"
          }`}>
            {source.status === "connected" ? "Connected" : source.status === "ready" ? "Ready" : "Off"}
          </span>
        </p>
        <p className="truncate text-xs text-ink-500">
          {source.detail}
          {source.last_sync && source.status === "connected" ? ` · ${timeAgo(source.last_sync)}` : ""}
        </p>
      </div>
      {children}
    </div>
  );
}

function btn(primary: boolean): string {
  return `flex shrink-0 items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold transition disabled:opacity-40 ${
    primary ? "bg-ink-900 text-white hover:bg-ink-800" : "border border-ink-200 text-ink-700 hover:bg-ink-50"
  }`;
}

const ICON_BG: Record<SourceId, string> = {
  imessage: "bg-[#e3f1ff] text-[#1f5fa3]",
  whatsapp: "bg-[#dff5e6] text-[#1d7a43]",
  contacts: "bg-ink-100 text-ink-700",
  gmail: "bg-[#fde8e6] text-[#a1352b]",
  calendar: "bg-[#fff2d9] text-[#8a5a00]",
};

export function SourceIcon({ id }: { id: SourceId }) {
  const paths: Record<SourceId, React.ReactNode> = {
    imessage: <path d="M4 5h16v10H9l-5 4V5Z" strokeLinejoin="round" />,
    whatsapp: <path d="M5 19l1.2-3.6A7.5 7.5 0 1 1 9 18.4L5 19Z" strokeLinejoin="round" />,
    contacts: <><circle cx="12" cy="9" r="3.2" /><path d="M6 19c.8-3 3.2-4.5 6-4.5s5.2 1.5 6 4.5" strokeLinecap="round" /></>,
    gmail: <><rect x="3.5" y="5.5" width="17" height="13" rx="1.5" /><path d="m4 7 8 6 8-6" strokeLinejoin="round" /></>,
    calendar: <><rect x="4" y="5.5" width="16" height="14" rx="1.5" /><path d="M4 10h16M8.5 3.5v4M15.5 3.5v4" strokeLinecap="round" /></>,
  };
  return (
    <span className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg ${ICON_BG[id]}`}>
      <svg className="h-[18px] w-[18px]" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden>
        {paths[id]}
      </svg>
    </span>
  );
}

function GoogleG() {
  return (
    <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" aria-hidden>
      <path fill="#fff" d="M21.6 12.2c0-.7-.1-1.4-.2-2H12v3.8h5.4a4.6 4.6 0 0 1-2 3v2.5h3.2c1.9-1.7 3-4.3 3-7.3Z" />
      <path fill="#fff" opacity=".85" d="M12 22c2.7 0 5-.9 6.6-2.4l-3.2-2.5c-.9.6-2 1-3.4 1-2.6 0-4.8-1.8-5.6-4.1H3.1v2.6A10 10 0 0 0 12 22Z" />
      <path fill="#fff" opacity=".7" d="M6.4 14c-.2-.6-.3-1.3-.3-2s.1-1.4.3-2V7.4H3.1A10 10 0 0 0 2 12c0 1.6.4 3.2 1.1 4.6L6.4 14Z" />
      <path fill="#fff" opacity=".55" d="M12 5.9c1.5 0 2.8.5 3.8 1.5l2.8-2.8A10 10 0 0 0 3.1 7.4L6.4 10C7.2 7.7 9.4 5.9 12 5.9Z" />
    </svg>
  );
}
