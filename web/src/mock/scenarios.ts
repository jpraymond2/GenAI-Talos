import type {
  Brief,
  CriticVerdict,
  Evidence,
  RunState,
  TraceEvent,
} from "../types";
import { DEFAULT_SOURCES_PLAN } from "../types";

const RUN_CLARIFY = "mock-run-clarify";
const RUN_BRIEF = "mock-run-brief";

function iso(offsetMs = 0): string {
  return new Date(Date.UTC(2026, 9, 15, 12, 0, 0) + offsetMs).toISOString();
}

function evt(
  run_id: string,
  agent: string,
  kind: string,
  payload: Record<string, unknown>,
  offsetMs: number,
): TraceEvent {
  return { ts: iso(offsetMs), run_id, agent, kind, payload };
}

const evidence: Record<string, Evidence> = {
  ev_seed: {
    id: "ev_seed",
    source: "gmail",
    source_ref: "gmail:fcc03c83f9",
    ts: "2026-06-12T18:10:00Z",
    text: "Hey Alex — quick update from Payloop. We're iterating on the spend dashboard ahead of the seed round. Free for a coffee next week?",
    url: null,
    tags: ["seed", "payloop"],
  },
  ev_lina: {
    id: "ev_lina",
    source: "gmail",
    source_ref: "gmail:644c7c5984",
    ts: "2026-09-26T10:14:00Z",
    text: "Intro ask: Rachel Goldberg? — Alex — any chance you could intro me to Rachel Goldberg at Northwind? She's the associate I mentioned; would love 20 minutes on the seed narrative.",
    url: null,
    tags: ["open_loop", "intro"],
  },
  ev_city_wa: {
    id: "ev_city_wa",
    source: "whatsapp",
    source_ref: "whatsapp:b3dc0144ac",
    ts: "2026-06-08T21:05:00Z",
    text: "I'll be in Toronto through January — ping me if you're up that way.",
    url: null,
    tags: ["conflict", "city"],
  },
  ev_city_contact: {
    id: "ev_city_contact",
    source: "imessage",
    source_ref: "contact:c_omar_haddad",
    ts: "2026-01-01T00:00:00Z",
    text: "Contact card lists city as Montreal (Payloop / Founder).",
    url: null,
    tags: ["conflict", "city"],
  },
  ev_coffee: {
    id: "ev_coffee",
    source: "calendar",
    source_ref: "calendar:coffee_omar",
    ts: "2026-06-15T15:00:00Z",
    text: "Coffee w/ Omar — 2026-06-15 11:00 ET, Desautels.",
    url: null,
    tags: ["logistics"],
  },
  ev_mem: {
    id: "ev_mem",
    source: "memory",
    source_ref: "hmem:mem_seed",
    ts: "2026-06-12T18:40:00Z",
    text: "Omar is raising a seed round for Payloop this quarter.",
    url: null,
    tags: ["memory"],
  },
};

const draft1: Brief = {
  person: "Omar Haddad",
  headline: "Seed momentum, one open intro, and a city conflict to clear",
  points: [
    {
      text: "Ask how the Payloop seed round is tracking after the spend-dashboard push.",
      evidence_ids: ["ev_seed", "ev_mem"],
      kind: "catch_up",
      priority: 1,
    },
    {
      text: "You still haven't replied to his Rachel Goldberg intro — close that loop.",
      evidence_ids: ["ev_lina"],
      kind: "open_loop",
      priority: 2,
    },
    {
      text: "He's based in Toronto now.",
      evidence_ids: ["ev_city_wa"],
      kind: "logistics",
      priority: 3,
    },
  ],
  caveats: [],
};

const draft2: Brief = {
  person: "Omar Haddad",
  headline: "Seed momentum, one open intro, and a city conflict to clear",
  points: [
    {
      text: "Ask how the Payloop seed round is tracking after the spend-dashboard push.",
      evidence_ids: ["ev_seed", "ev_mem"],
      kind: "catch_up",
      priority: 1,
    },
    {
      text: "You still haven't replied to his Rachel Goldberg intro — close that loop.",
      evidence_ids: ["ev_lina"],
      kind: "open_loop",
      priority: 2,
    },
    {
      text: "Sources disagree on city: contact says Montreal, WhatsApp says Toronto through January — confirm which is current.",
      evidence_ids: ["ev_city_wa", "ev_city_contact"],
      kind: "logistics",
      priority: 3,
    },
  ],
  caveats: ["City conflict: Montreal vs Toronto"],
};

const verdictRevise: CriticVerdict = {
  overall: "revise",
  verdicts: [
    {
      index: 0,
      decision: "keep",
      reason: "Seed update is well-sourced",
      fix_hint: null,
    },
    {
      index: 1,
      decision: "keep",
          reason: "Clear open loop with Gmail evidence",
      fix_hint: null,
    },
    {
      index: 2,
      decision: "fix",
      reason: "Asserts Toronto as fact despite conflicting contact card",
      fix_hint: "Flag Montreal vs Toronto as a conflict, do not pick a side",
    },
  ],
  missing: [],
  refetch: [],
};

const verdictPass: CriticVerdict = {
  overall: "pass",
  verdicts: [
    { index: 0, decision: "keep", reason: "Still solid", fix_hint: null },
    { index: 1, decision: "keep", reason: "Open loop intact", fix_hint: null },
    {
      index: 2,
      decision: "conflict",
      reason: "Correctly surfaces the city disagreement",
      fix_hint: null,
    },
  ],
  missing: [],
  refetch: [],
};

export function clarificationState(text: string): RunState {
  return {
    run_id: RUN_CLARIFY,
    utterance: { text, ts: iso(), mode: "text" },
    intent: {
      kind: "brief",
      person_name: "Omar",
      when: null,
      action_request: null,
      sources_plan: [...DEFAULT_SOURCES_PLAN],
      confidence: 0.82,
    },
    candidates: [
      {
        entity_id: "omar-haddad",
        name: "Omar Haddad",
        score: 0.55,
        reason: "Payloop founder in your messages",
      },
      {
        entity_id: "omar-kassem",
        name: "Omar Kassem",
        score: 0.45,
        reason: "Appears in a class/finance thread",
      },
    ],
    entity_id: null,
    entity_name: null,
    context: {},
    memories: [],
    evidence: {},
    drafts: [],
    verdicts: [],
    final_brief: null,
    action_plan: {
      steps: [
        {
          tool: "calendar.hold",
          args: { title: "Coffee w/ Omar (TBD which)" },
          summary: "Hold a 30m calendar slot once you pick which Omar",
        },
      ],
      spoken_summary: "I can hold a calendar slot once you clarify which Omar.",
      requires_confirmation: true,
    },
    status: "needs_clarification",
    round: 0,
    question: "Omar Haddad or Omar Kassem?",
  };
}

export function briefState(text: string): RunState {
  return {
    run_id: RUN_BRIEF,
    utterance: { text, ts: iso(), mode: "text" },
    intent: {
      kind: "brief",
      person_name: "Omar Haddad",
      when: null,
      action_request: null,
      sources_plan: [...DEFAULT_SOURCES_PLAN],
      confidence: 0.94,
    },
    candidates: [
      {
        entity_id: "omar-haddad",
        name: "Omar Haddad",
        score: 0.96,
        reason: "Exact name match",
      },
    ],
    entity_id: "omar-haddad",
    entity_name: "Omar Haddad",
    context: { latency_ms: 4820, llm_calls: 4, critic_rounds: 2 },
    memories: [
      {
        id: "mem_seed",
        text: "Omar is raising a seed round for Payloop this quarter.",
        score: 0.91,
      },
    ],
    evidence,
    drafts: [draft1, draft2],
    verdicts: [verdictRevise, verdictPass],
    final_brief: draft2,
    action_plan: {
      steps: [
        {
          tool: "gmail.draft_reply",
          args: {
            thread: "th_kh_lina_intro",
            subject: "Intro ask: Rachel Goldberg?",
            to: "omar@payloop.io",
          },
          summary: "Draft a short reply closing the Rachel Goldberg intro loop",
        },
        {
          tool: "calendar.create",
          args: { title: "Coffee w/ Omar", location: "Desautels" },
          summary: "Offer a coffee hold matching your usual Desautels pattern",
        },
      ],
      spoken_summary:
        "I can draft the Lina reply and hold coffee — confirm when you're ready.",
      requires_confirmation: true,
    },
    status: "awaiting_confirmation",
    round: 2,
    question: null,
  };
}

export function clarificationTrace(): TraceEvent[] {
  const id = RUN_CLARIFY;
  return [
    evt(id, "router", "complete", {
      summary: "brief for person_name=Omar",
      model: "nvidia/nemotron-3-ultra-550b-a55b",
      latency_ms: 420,
      intent: "brief",
    }, 100),
    evt(id, "resolver", "complete", {
      summary: "ambiguous: Omar Haddad vs Omar Kassem",
      model: "fast",
      latency_ms: 180,
      candidates: 2,
    }, 700),
    evt(id, "orchestrator", "clarify", {
      summary: "Omar Haddad or Omar Kassem?",
      latency_ms: 12,
    }, 900),
  ];
}

/** Full brief path: router → resolver → retriever (USL) | memory → writer → critic revise → writer → critic pass. */
export function briefTrace(): TraceEvent[] {
  const id = RUN_BRIEF;
  return [
    evt(id, "router", "complete", {
      summary: "brief for Omar Haddad",
      model: "nvidia/nemotron-3-ultra-550b-a55b",
      latency_ms: 390,
    }, 80),
    evt(id, "resolver", "complete", {
      summary: "resolved omar-haddad (score 0.96)",
      model: "world+hmem",
      latency_ms: 210,
      entity_id: "omar-haddad",
    }, 400),
    evt(id, "retriever", "query", {
      summary: "via Zetaris USL",
      backend: "zetaris",
      zetaris_mode: "usl",
      usl_tables: [
        "lightning.metastore.recallai.recall.handle",
        "lightning.metastore.recallai.recall.interaction",
        "lightning.metastore.recallai.recall.event",
      ],
      latency_ms: 2400,
      row_count: 32,
    }, 900),
    evt(id, "memory", "complete", {
      summary: "3 memories retrieved for omar-haddad",
      model: "nvidia/nv-embedqa-e5-v5",
      latency_ms: 310,
    }, 1400),
    evt(id, "writer", "complete", {
      summary: "draft v1 — 3 talking points",
      model: "nvidia/nemotron-3-ultra-550b-a55b",
      latency_ms: 980,
      round: 1,
    }, 2200),
    evt(id, "critic", "complete", {
      summary: "revise — fix city assertion",
      model: "nvidia/nemotron-3-super-120b-a12b",
      latency_ms: 640,
      overall: "revise",
      verdicts: verdictRevise.verdicts,
    }, 3000),
    evt(id, "writer", "complete", {
      summary: "draft v2 — city flagged as conflict",
      model: "nvidia/nemotron-3-ultra-550b-a55b",
      latency_ms: 870,
      round: 2,
    }, 4000),
    evt(id, "critic", "complete", {
      summary: "pass",
      model: "nvidia/nemotron-3-super-120b-a12b",
      latency_ms: 520,
      overall: "pass",
      verdicts: verdictPass.verdicts,
    }, 4800),
    evt(id, "orchestrator", "complete", {
      summary: "status=awaiting_confirmation rounds=2 llm_calls=4",
      latency_ms: 4820,
      status: "awaiting_confirmation",
    }, 5000),
  ];
}

export function pickScenario(text: string): {
  state: RunState;
  traces: TraceEvent[];
} {
  const normalized = text.trim().toLowerCase();
  if (
    normalized.includes("omar haddad") ||
    normalized === "omar-haddad" ||
    normalized.includes("confirm haddad")
  ) {
    return { state: briefState(text), traces: briefTrace() };
  }
  if (normalized.includes("omar saleh") || normalized === "omar-kassem") {
    const state = briefState(text);
    state.entity_id = "omar-kassem";
    state.entity_name = "Omar Kassem";
    state.final_brief = {
      person: "Omar Kassem",
      headline: "Teaching assistant at McGill — light context",
      points: [
        {
          text: "He's a TA at McGill; keep it short unless class topics come up.",
          evidence_ids: [],
          kind: "catch_up",
          priority: 1,
        },
      ],
      caveats: ["Thin evidence vs Omar Haddad"],
    };
    state.action_plan = null;
    state.status = "done";
    return { state, traces: briefTrace().map((t) => ({ ...t, run_id: state.run_id })) };
  }
  if (normalized.includes("omar")) {
    return { state: clarificationState(text), traces: clarificationTrace() };
  }
  return {
    state: {
      ...clarificationState(text),
      question: "Who are you meeting?",
      candidates: [],
      action_plan: null,
    },
    traces: clarificationTrace(),
  };
}
