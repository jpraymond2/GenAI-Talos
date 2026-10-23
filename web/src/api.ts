import { pickScenario } from "./mock/scenarios";
import type { RunState, TraceEvent } from "./types";

const MOCK_FLAG = String(import.meta.env.VITE_MOCK ?? "true").toLowerCase() === "true";

type TraceListener = (event: TraceEvent) => void;

const listeners = new Set<TraceListener>();
let mockTimers: number[] = [];
let liveSocket: WebSocket | null = null;
let forceMock: boolean | null = null;

export function onTrace(listener: TraceListener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function emit(event: TraceEvent): void {
  for (const listener of listeners) listener(event);
}

function clearMockTimers(): void {
  for (const id of mockTimers) window.clearTimeout(id);
  mockTimers = [];
}

function useMock(): boolean {
  if (forceMock === true) return true;
  if (forceMock === false) return false;
  return MOCK_FLAG;
}

async function backendReachable(): Promise<boolean> {
  try {
    const ctrl = new AbortController();
    const timer = window.setTimeout(() => ctrl.abort(), 1500);
    const res = await fetch("/health", { signal: ctrl.signal });
    window.clearTimeout(timer);
    return res.ok;
  } catch {
    return false;
  }
}

async function shouldMock(): Promise<boolean> {
  if (useMock()) return true;
  const ok = await backendReachable();
  if (!ok) {
    forceMock = true;
    return true;
  }
  return false;
}

function replayTraces(traces: TraceEvent[], gapMs = 280): Promise<void> {
  clearMockTimers();
  return new Promise((resolve) => {
    traces.forEach((event, index) => {
      const id = window.setTimeout(() => {
        emit(event);
        if (index === traces.length - 1) resolve();
      }, gapMs * (index + 1));
      mockTimers.push(id);
    });
    if (traces.length === 0) resolve();
  });
}

function connectLiveTrace(): void {
  if (liveSocket || typeof window === "undefined") return;
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  const url = `${proto}://${window.location.host}/ws/trace`;
  try {
    liveSocket = new WebSocket(url);
    liveSocket.onmessage = (msg) => {
      try {
        emit(JSON.parse(String(msg.data)) as TraceEvent);
      } catch {
        /* ignore malformed frames */
      }
    };
    liveSocket.onclose = () => {
      liveSocket = null;
    };
  } catch {
    liveSocket = null;
  }
}

/** POST /api/ask { text } — or mock replay when VITE_MOCK / unreachable. */
export async function ask(text: string): Promise<RunState> {
  const mock = await shouldMock();
  if (mock) {
    const { state, traces } = pickScenario(text);
    void replayTraces(traces);
    return structuredClone(state);
  }
  connectLiveTrace();
  const res = await fetch("/api/ask", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
  if (!res.ok) {
    forceMock = true;
    const { state, traces } = pickScenario(text);
    void replayTraces(traces);
    return structuredClone(state);
  }
  return (await res.json()) as RunState;
}

/** POST /api/confirm — entity pick or action confirmation. */
export async function confirm(payload: {
  entity_id?: string;
  action?: "confirm" | "cancel";
  run_id?: string;
}): Promise<RunState> {
  const mock = await shouldMock();
  if (mock) {
    if (payload.action === "cancel") {
      const { state } = pickScenario("I'm meeting Karim Haddad");
      state.status = "done";
      state.action_plan = null;
      return structuredClone(state);
    }
    if (payload.action === "confirm") {
      const { state } = pickScenario("I'm meeting Karim Haddad");
      state.status = "done";
      return structuredClone(state);
    }
    const name =
      payload.entity_id === "karim-saleh"
        ? "I'm meeting Karim Saleh"
        : "I'm meeting Karim Haddad";
    const { state, traces } = pickScenario(name);
    void replayTraces(traces);
    return structuredClone(state);
  }
  connectLiveTrace();
  const res = await fetch("/api/confirm", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    forceMock = true;
    return confirm(payload);
  }
  return (await res.json()) as RunState;
}

/** MediaRecorder blob → POST /api/voice (inert in mock). */
export async function sendVoice(blob: Blob): Promise<{ text: string } | null> {
  if (await shouldMock()) {
    return null;
  }
  const body = new FormData();
  body.append("audio", blob, "utterance.webm");
  const res = await fetch("/api/voice", { method: "POST", body });
  if (!res.ok) return null;
  return (await res.json()) as { text: string };
}

export function isMockMode(): boolean {
  return useMock();
}
