import { karimLedger, karimMemories, karimPerson } from "./mock/person";
import { pickScenario } from "./mock/scenarios";
import type {
  LedgerEvent,
  MemoryItem,
  PersonView,
  RunState,
  TraceEvent,
} from "./types";

const MOCK_FLAG = String(import.meta.env.VITE_MOCK ?? "true").toLowerCase() === "true";
const MOCK_VOICE_TRANSCRIPT = "I'm meeting Karim Haddad";

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

function sourcesFromContext(ctx: Record<string, unknown>): string[] {
  const last = ctx.last_interaction_per_source;
  if (last && typeof last === "object") {
    return Object.keys(last as Record<string, unknown>).sort();
  }
  return [];
}

function mapPersonContext(raw: Record<string, unknown>): PersonView {
  const entity = (raw.entity as Record<string, unknown> | undefined) ?? {};
  const attributes = (entity.attributes as Record<string, string> | undefined) ?? {};
  const conflictsRaw = (raw.conflicts as Array<Record<string, unknown>> | undefined) ?? [];
  return {
    entity_id: String(entity.id ?? raw.entity_id ?? ""),
    canonical_name: String(entity.canonical_name ?? ""),
    aliases: Array.isArray(raw.aliases)
      ? (raw.aliases as string[])
      : Array.isArray(entity.aliases)
        ? (entity.aliases as string[])
        : [],
    sources: sourcesFromContext(raw),
    attributes,
    conflicts: conflictsRaw.map((c) => ({
      field: String(c.field ?? ""),
      values: Array.isArray(c.values)
        ? (c.values as Array<Record<string, unknown>>).map((v) => ({
            value: String(v.value ?? ""),
            source_ref: String(v.source_ref ?? ""),
          }))
        : [],
    })),
  };
}

function mapMemories(raw: Record<string, unknown>): MemoryItem[] {
  const rows = (raw.memories as Array<Record<string, unknown>> | undefined) ?? [];
  return rows.map((m) => ({
    id: String(m.id ?? ""),
    text: String(m.content ?? m.text ?? ""),
    score: Number(m.score ?? 0),
    why: String(m.why ?? ""),
    ts: String(m.ts ?? m.timestamp ?? ""),
  }));
}

/** POST /api/ask { text, entity_id? } — or mock replay when VITE_MOCK / unreachable. */
export async function ask(text: string, entityId?: string | null): Promise<RunState> {
  const mock = await shouldMock();
  if (mock) {
    const key =
      entityId === "karim-saleh"
        ? "I'm meeting Karim Saleh"
        : entityId
          ? "I'm meeting Karim Haddad"
          : text;
    const { state, traces } = pickScenario(key);
    void replayTraces(traces);
    return structuredClone(state);
  }
  connectLiveTrace();
  const res = await fetch("/api/ask", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, entity_id: entityId ?? null }),
  });
  if (!res.ok) {
    forceMock = true;
    const { state, traces } = pickScenario(text);
    void replayTraces(traces);
    return structuredClone(state);
  }
  return (await res.json()) as RunState;
}

/** POST /api/confirm { run_id, confirmed } — actions dry-run when implemented. */
export async function confirm(payload: {
  run_id: string;
  confirmed: boolean;
}): Promise<{ status: string; run_id?: string }> {
  const mock = await shouldMock();
  if (mock) {
    return {
      status: payload.confirmed ? "confirmed" : "cancelled",
      run_id: payload.run_id,
    };
  }
  connectLiveTrace();
  const res = await fetch("/api/confirm", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    return { status: "error", run_id: payload.run_id };
  }
  return (await res.json()) as { status: string; run_id?: string };
}

/** GET /api/person/{entity_id} — mock Karim when offline. */
export async function fetchPerson(entityId: string): Promise<PersonView | null> {
  if (await shouldMock()) {
    if (!entityId || entityId === "karim-haddad") return structuredClone(karimPerson);
    return null;
  }
  const res = await fetch(`/api/person/${encodeURIComponent(entityId)}`);
  if (res.status === 404) return null;
  if (!res.ok) return null;
  return mapPersonContext((await res.json()) as Record<string, unknown>);
}

/** GET /api/memories/{entity_id} — mock Karim memories when offline. */
export async function fetchMemories(
  entityId: string,
): Promise<{ memories: MemoryItem[]; ledger: LedgerEvent[] }> {
  if (await shouldMock()) {
    if (!entityId || entityId === "karim-haddad") {
      return {
        memories: structuredClone(karimMemories),
        ledger: structuredClone(karimLedger),
      };
    }
    return { memories: [], ledger: [] };
  }
  const res = await fetch(`/api/memories/${encodeURIComponent(entityId)}`);
  if (!res.ok) return { memories: [], ledger: [] };
  return {
    memories: mapMemories((await res.json()) as Record<string, unknown>),
    ledger: [],
  };
}

export interface VoiceResult {
  transcript: string;
  engine?: string;
  speech?: string;
  status?: string;
  headline?: string;
  points?: string[];
  question?: string;
}

/** MediaRecorder blob → POST /api/voice (mock returns fixed transcript). */
export async function sendVoice(blob: Blob): Promise<VoiceResult | null> {
  if (await shouldMock()) {
    return { transcript: MOCK_VOICE_TRANSCRIPT, engine: "sample" };
  }
  const body = new FormData();
  const ext = blob.type.includes("mp4") ? "mp4" : "webm";
  body.append("audio", blob, `utterance.${ext}`);
  const res = await fetch("/api/voice", { method: "POST", body });
  if (!res.ok) return null;
  const data = (await res.json()) as VoiceResult;
  if (!data.transcript) return null;
  return data;
}

export function isMockMode(): boolean {
  return useMock();
}
