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
    text: "Hey Alex — quick update from Ledgerly. We're iterating on the spend dashboard ahead of the seed round. Free for a coffee next week?",
    url: null,
    tags: ["seed", "ledgerly"],
  },
  ev_lina: {
    id: "ev_lina",
    source: "gmail",
    source_ref: "gmail:644c7c5984",
    ts: "2026-09-26T10:14:00Z",
    text: "Intro ask: Lina Chen? — Alex — any chance you could intro me to Lina Chen at Northbridge? She's the associate I mentioned; would love 20 minutes on the seed narrative.",
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
    source_ref: "contact:c_karim_haddad",
    ts: "2026-01-01T00:00:00Z",
    text: "Contact card lists city as Montreal (Ledgerly / Founder).",
    url: null,
    tags: ["conflict", "city"],
  },
  ev_coffee: {
    id: "ev_coffee",
    source: "calendar",
    source_ref: "calendar:coffee_karim",
    ts: "2026-06-15T15:00:00Z",
    text: "Coffee w/ Karim — 2026-06-15 11:00 ET, Desautels.",
    url: null,
    tags: ["logistics"],
  },
  ev_mem: {
    id: "ev_mem",
    source: "memory",
    source_ref: "hmem:mem_seed",
    ts: "2026-06-12T18:40:00Z",
    text: "Karim is raising a seed round for Ledgerly this quarter.",
    url: null,
    tags: ["memory"],
  },
};

const draft1: Brief = {
  person: "Karim Haddad",
  headline: "Seed momentum, one open intro, and a city conflict to clear",
  points: [
    {
      text: "Ask how the Ledgerly seed round is tracking after the spend-dashboard push.",
      evidence_ids: ["ev_seed", "ev_mem"],
      kind: "catch_up",
      priority: 1,
    },
    {
      text: "You still haven't replied to his Lina Chen intro — close that loop.",
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
  person: "Karim Haddad",
  headline: "Seed momentum, one open intro, and a city conflict to clear",
  points: [
    {
      text: "Ask how the Ledgerly seed round is tracking after the spend-dashboard push.",
      evidence_ids: ["ev_seed", "ev_mem"],
      kind: "catch_up",
      priority: 1,
    },
    {
      text: "You still haven't replied to his Lina Chen intro — close that loop.",
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
      person_name: "Karim",
      when: null,
      action_request: null,
      sources_plan: [...DEFAULT_SOURCES_PLAN],
      confidence: 0.82,
    },
    candidates: [
      {
        entity_id: "karim-haddad",
        name: "Karim Haddad",
        score: 0.55,
        reason: "Ledgerly founder in your messages",
      },
      {
        entity_id: "karim-saleh",
        name: "Karim Saleh",
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
          args: { title: "Coffee w/ Karim (TBD which)" },
          summary: "Hold a 30m calendar slot once you pick which Karim",
        },
      ],
      spoken_summary: "I can hold a calendar slot once you clarify which Karim.",
      requires_confirmation: true,
    },
    status: "needs_clarification",
    round: 0,
    question: "Karim Haddad or Karim Saleh?",
  };
}

export function briefState(text: string): RunState {
  return {
    run_id: RUN_BRIEF,
    utterance: { text, ts: iso(), mode: "text" },
    intent: {
      kind: "brief",
      person_name: "Karim Haddad",
      when: null,
      action_request: null,
      sources_plan: [...DEFAULT_SOURCES_PLAN],
      confidence: 0.94,
    },
    candidates: [
      {
        entity_id: "karim-haddad",
        name: "Karim Haddad",
        score: 0.96,
        reason: "Exact name match",
      },
    ],
    entity_id: "karim-haddad",
    entity_name: "Karim Haddad",
    context: { latency_ms: 4820, llm_calls: 4, critic_rounds: 2 },
    memories: [
      {
        id: "mem_seed",
        text: "Karim is raising a seed round for Ledgerly this quarter.",
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
            subject: "Intro ask: Lina Chen?",
            to: "karim@ledgerly.io",
          },
          summary: "Draft a short reply closing the Lina Chen intro loop",
        },
        {
          tool: "calendar.create",
          args: { title: "Coffee w/ Karim", location: "Desautels" },
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
      summary: "brief for person_name=Karim",
      model: "nvidia/nemotron-3-ultra-550b-a55b",
      latency_ms: 420,
      intent: "brief",
    }, 100),
    evt(id, "resolver", "complete", {
      summary: "ambiguous: Karim Haddad vs Karim Saleh",
      model: "fast",
      latency_ms: 180,
      candidates: 2,
    }, 700),
    evt(id, "orchestrator", "clarify", {
      summary: "Karim Haddad or Karim Saleh?",
      latency_ms: 12,
    }, 900),
  ];
}

/** Full brief path: router → resolver → retriever (USL) | memory → writer → critic revise → writer → critic pass. */
export function briefTrace(): TraceEvent[] {
  const id = RUN_BRIEF;
  return [
    evt(id, "router", "complete", {
      summary: "brief for Karim Haddad",
      model: "nvidia/nemotron-3-ultra-550b-a55b",
      latency_ms: 390,
    }, 80),
    evt(id, "resolver", "complete", {
      summary: "resolved karim-haddad (score 0.96)",
      model: "world+hmem",
      latency_ms: 210,
      entity_id: "karim-haddad",
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
      summary: "3 memories retrieved for karim-haddad",
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
    normalized.includes("karim haddad") ||
    normalized === "karim-haddad" ||
    normalized.includes("confirm haddad")
  ) {
    return { state: briefState(text), traces: briefTrace() };
  }
  if (normalized.includes("karim saleh") || normalized === "karim-saleh") {
    const state = briefState(text);
    state.entity_id = "karim-saleh";
    state.entity_name = "Karim Saleh";
    state.final_brief = {
      person: "Karim Saleh",
      headline: "Teaching assistant at McGill — light context",
      points: [
        {
          text: "He's a TA at McGill; keep it short unless class topics come up.",
          evidence_ids: [],
          kind: "catch_up",
          priority: 1,
        },
      ],
      caveats: ["Thin evidence vs Karim Haddad"],
    };
    state.action_plan = null;
    state.status = "done";
    return { state, traces: briefTrace().map((t) => ({ ...t, run_id: state.run_id })) };
  }
  if (normalized.includes("karim")) {
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
