import {
  authHeadersAsync,
  ensureApiToken,
  ensureApiTokenAsync,
  getApiToken,
} from "./auth";
import { omarLedger, omarMemories, omarPerson } from "./mock/person";
import { pickScenario } from "./mock/scenarios";
import { recordingFilename } from "./recorderMime";
import type {
  LedgerEvent,
  MemoryItem,
  PersonView,
  RunState,
  TraceEvent,
} from "./types";

const MOCK_FLAG = String(import.meta.env.VITE_MOCK ?? "false").toLowerCase() === "true";
const MOCK_VOICE_TRANSCRIPT = "I'm meeting Omar Haddad";

type TraceListener = (event: TraceEvent) => void;

const listeners = new Set<TraceListener>();
let mockTimers: number[] = [];
let liveSocket: WebSocket | null = null;
let forceMock: boolean | null = null;
let backendDown = false;

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

export function isBackendUnreachable(): boolean {
  return backendDown;
}

export function enableRecordedDemoMock(): void {
  forceMock = true;
  backendDown = false;
}

export function clearForcedMock(): void {
  forceMock = null;
}

/** True only when mock is already on (VITE_MOCK or recorded-demo). Never silently forces mock. */
export async function shouldMock(): Promise<boolean> {
  if (useMock()) return true;
  // Probe reachability for the banner; do not flip forceMock here.
  const ok = await backendReachable();
  backendDown = !ok;
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
  const token = ensureApiToken();
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  const q = token ? `?token=${encodeURIComponent(token)}` : "";
  const url = `${proto}://${window.location.host}/ws/trace${q}`;
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

/** POST /api/ask { text, run_id?, entity_id? }. */
export async function ask(
  text: string,
  opts?: { entityId?: string | null; runId?: string | null },
): Promise<RunState> {
  const entityId = opts?.entityId;
  const runId = opts?.runId;
  if (MOCK_FLAG || forceMock === true) {
    const key =
      entityId === "omar-kassem"
        ? "I'm meeting Omar Kassem"
        : entityId
          ? "I'm meeting Omar Haddad"
          : text;
    const { state, traces } = pickScenario(key);
    void replayTraces(traces);
    return structuredClone(state);
  }
  const reachable = await backendReachable();
  backendDown = !reachable;
  if (!reachable) {
    throw new Error("Backend not reachable on :8000; run `make demo`");
  }
  await ensureApiTokenAsync();
  connectLiveTrace();
  const res = await fetch("/api/ask", {
    method: "POST",
    headers: await authHeadersAsync({ "Content-Type": "application/json" }),
    body: JSON.stringify({
      text,
      entity_id: entityId ?? null,
      run_id: runId ?? null,
    }),
  });
  if (!res.ok) {
    if (res.status === 401) {
      throw new Error("Unauthorized — check RECALL_API_TOKEN");
    }
    throw new Error(`Ask failed (${res.status})`);
  }
  return (await res.json()) as RunState;
}

/** POST /api/confirm { run_id, confirmed }. */
export async function confirm(payload: {
  run_id: string;
  confirmed: boolean;
}): Promise<{ status: string; run_id?: string }> {
  if (MOCK_FLAG || forceMock === true) {
    return {
      status: payload.confirmed ? "confirmed" : "cancelled",
      run_id: payload.run_id,
    };
  }
  await ensureApiTokenAsync();
  connectLiveTrace();
  const res = await fetch("/api/confirm", {
    method: "POST",
    headers: await authHeadersAsync({ "Content-Type": "application/json" }),
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    return { status: "error", run_id: payload.run_id };
  }
  return (await res.json()) as { status: string; run_id?: string };
}

/** GET /api/person/{entity_id}. */
export async function fetchPerson(entityId: string): Promise<PersonView | null> {
  if (MOCK_FLAG || forceMock === true) {
    if (!entityId || entityId === "omar-haddad") return structuredClone(omarPerson);
    return null;
  }
  await ensureApiTokenAsync();
  const res = await fetch(`/api/person/${encodeURIComponent(entityId)}`, {
    headers: await authHeadersAsync(),
  });
  if (res.status === 404) return null;
  if (!res.ok) return null;
  return mapPersonContext((await res.json()) as Record<string, unknown>);
}

/** GET /api/memories/{entity_id}. */
export async function fetchMemories(
  entityId: string,
): Promise<{ memories: MemoryItem[]; ledger: LedgerEvent[] }> {
  if (MOCK_FLAG || forceMock === true) {
    if (!entityId || entityId === "omar-haddad") {
      return {
        memories: structuredClone(omarMemories),
        ledger: structuredClone(omarLedger),
      };
    }
    return { memories: [], ledger: [] };
  }
  await ensureApiTokenAsync();
  const res = await fetch(`/api/memories/${encodeURIComponent(entityId)}`, {
    headers: await authHeadersAsync(),
  });
  if (!res.ok) return { memories: [], ledger: [] };
  return {
    memories: mapMemories((await res.json()) as Record<string, unknown>),
    ledger: [],
  };
}

export interface VoiceResult extends Partial<RunState> {
  transcript: string;
  engine?: string;
  speech?: string;
  voice_timing?: Record<string, number | string>;
}

export type SendVoiceOutcome =
  | { ok: true; data: VoiceResult }
  | { ok: false; error: string };

function formatHttpDetail(status: number, body: unknown): string {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string" && detail.trim()) {
      return `${status}: ${detail}`;
    }
    if (Array.isArray(detail)) {
      const parts = detail
        .map((item) =>
          item && typeof item === "object" && "msg" in item
            ? String((item as { msg: unknown }).msg)
            : JSON.stringify(item),
        )
        .filter(Boolean);
      if (parts.length) return `${status}: ${parts.join("; ")}`;
    }
  }
  return `${status}: Voice upload failed`;
}

/** MediaRecorder blob → POST /api/voice (same ask JSON + transcript). */
export async function sendVoice(
  blob: Blob,
  opts?: { runId?: string | null },
): Promise<SendVoiceOutcome> {
  if (MOCK_FLAG || forceMock === true) {
    return {
      ok: true,
      data: { transcript: MOCK_VOICE_TRANSCRIPT, engine: "sample" },
    };
  }
  await ensureApiTokenAsync();
  const body = new FormData();
  body.append("audio", blob, recordingFilename(blob.type || "audio/webm"));
  if (opts?.runId) body.append("run_id", opts.runId);
  let res: Response;
  try {
    res = await fetch("/api/voice", {
      method: "POST",
      headers: await authHeadersAsync(),
      body,
    });
  } catch (err) {
    const msg = err instanceof Error ? err.message : "network error";
    return { ok: false, error: `Voice upload failed: ${msg}` };
  }
  let parsed: unknown = null;
  try {
    parsed = await res.json();
  } catch {
    parsed = null;
  }
  if (!res.ok) {
    return { ok: false, error: formatHttpDetail(res.status, parsed) };
  }
  const data = parsed as VoiceResult;
  if (!data?.transcript) {
    return { ok: false, error: "Voice endpoint returned no transcript." };
  }
  return { ok: true, data };
}

export function isMockMode(): boolean {
  return useMock();
}

export function hasApiTokenConfigured(): boolean {
  return Boolean(getApiToken());
}
