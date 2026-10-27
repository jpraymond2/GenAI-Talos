/** Mirrors recall/schemas.py from origin/agent-brain exactly. */

export type EvidenceSource =
  | "gmail"
  | "imessage"
  | "whatsapp"
  | "calendar"
  | "web"
  | "memory"
  | "linkedin";

export const DEFAULT_SOURCES_PLAN: string[] = [
  "gmail",
  "imessage",
  "whatsapp",
  "linkedin",
  "calendar",
  "web",
  "memory",
];

export interface Utterance {
  text: string;
  ts: string;
  mode: "voice" | "text";
}

export interface Intent {
  kind: "brief" | "note" | "action" | "question" | "unknown";
  person_name: string | null;
  when: string | null;
  action_request: string | null;
  sources_plan: string[];
  confidence: number;
}

export interface Entity {
  name: string;
  kind: string;
}

export interface Candidate {
  entity_id: string;
  name: string;
  score: number;
  reason: string;
}

export interface Evidence {
  id: string;
  source: EvidenceSource;
  source_ref: string;
  ts: string;
  text: string;
  url: string | null;
  tags: string[];
}

export interface TalkingPoint {
  text: string;
  evidence_ids: string[];
  kind: "catch_up" | "open_loop" | "news" | "logistics" | "memory";
  priority: number;
}

export interface Brief {
  person: string;
  headline: string;
  points: TalkingPoint[];
  caveats: string[];
}

export interface PointVerdict {
  index: number;
  decision: "keep" | "fix" | "drop" | "conflict";
  reason: string;
  fix_hint: string | null;
}

export interface CriticVerdict {
  overall: "pass" | "revise";
  verdicts: PointVerdict[];
  missing: string[];
  refetch: string[];
}

export interface ActionStep {
  tool: string;
  args: Record<string, unknown>;
  summary: string;
}

export interface ActionPlan {
  steps: ActionStep[];
  spoken_summary: string;
  requires_confirmation: boolean;
}

export type RunStatus =
  | "running"
  | "done"
  | "needs_clarification"
  | "awaiting_confirmation"
  | "failed";

export interface RunState {
  run_id: string;
  utterance: Utterance;
  intent: Intent | null;
  candidates: Candidate[];
  entity_id: string | null;
  entity_name: string | null;
  context: Record<string, unknown>;
  memories: Record<string, unknown>[];
  evidence: Record<string, Evidence>;
  drafts: Brief[];
  verdicts: CriticVerdict[];
  final_brief: Brief | null;
  action_plan: ActionPlan | null;
  status: RunStatus;
  round: number;
  question: string | null;
  /** Spoken summary from the API (headline + points, ≤90 words). */
  speech?: string;
}

/** Trace event shape from recall/tracing.py Tracer.event(). */
export interface TraceEvent {
  ts: string;
  run_id: string;
  agent: string;
  kind: string;
  payload: Record<string, unknown>;
}

/** Dashboard extras for Person / Memory tabs (not in RunState). */
export interface PersonView {
  entity_id: string;
  canonical_name: string;
  aliases: string[];
  sources: string[];
  attributes: Record<string, string>;
  conflicts: { field: string; values: { value: string; source_ref: string }[] }[];
}

export interface MemoryItem {
  id: string;
  text: string;
  score: number;
  why: string;
  ts: string;
}

export interface LedgerEvent {
  id: string;
  kind: string;
  summary: string;
  ts: string;
}

export interface TranscriptEntry {
  id: string;
  role: "user" | "assistant" | "system";
  text: string;
  ts: string;
}

/** A contact card from GET /api/people. */
export interface PersonCard {
  entity_id: string;
  name: string;
  other_names: string[];
  emails: string[];
  phones: string[];
  org: string;
  title: string;
  city: string;
  notes: string;
  edited_fields: string[];
  relationship: string;
  first_seen_at: string;
  last_seen_at: string;
  mentions: number;
  confidence: number;
  sources: string[];
  merged_from: number;
}

export interface PersonEdit {
  name?: string;
  org?: string;
  title?: string;
  city?: string;
  notes?: string;
  add_aliases?: string[];
  remove_aliases?: string[];
}

export type DeviceMode = "mac" | "phone";

export interface PullParams {
  days: number;
  sources: ("imessage" | "whatsapp" | "contacts")[];
  contacts: "all" | "selected";
  contact_ids: string[];
  contact_handles: string[];
  include_groups: boolean;
  max_group_size: number;
}

export type PullStatus = "queued" | "running" | "uploading" | "building" | "ready" | "failed" | "cancelled";

export interface PullJob {
  id: string;
  status: PullStatus;
  created_at: string;
  updated_at: string;
  params: PullParams;
  progress: Record<string, unknown>;
  error: string;
}

export interface DeviceStatus {
  connected: boolean;
  online: boolean;
  last_seen: string | null;
  device_id: string | null;
  agent_version: string | null;
  hostname?: string;
}

export interface MacContact {
  contact_id: string;
  name: string;
  organization: string;
  phones: string[];
  emails: string[];
}

export interface SessionUser {
  name: string;
  email: string;
}
