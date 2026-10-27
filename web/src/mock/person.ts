import type { LedgerEvent, MemoryItem, PersonView } from "../types";

export const omarPerson: PersonView = {
  entity_id: "omar-haddad",
  canonical_name: "Omar Haddad",
  aliases: ["Omar", "K. Haddad", "omar@payloop.io"],
  sources: ["contact", "gmail", "whatsapp", "imessage", "calendar"],
  attributes: {
    org: "Payloop",
    title: "Founder",
    city: "Montreal",
    email: "omar@payloop.io",
    phone: "+15145550142",
  },
  conflicts: [
    {
      field: "city",
      values: [
        { value: "Montreal", source_ref: "contact:c_omar_haddad" },
        { value: "Toronto", source_ref: "whatsapp:b3dc0144ac" },
      ],
    },
  ],
};

export const omarMemories: MemoryItem[] = [
  {
    id: "mem_seed",
    text: "Omar is raising a seed round for Payloop this quarter.",
    score: 0.91,
    why: "Repeated across Gmail threads and WhatsApp",
    ts: "2026-06-12T18:40:00Z",
  },
  {
    id: "mem_lina",
    text: "You still owe a reply on the Rachel Goldberg intro Omar sent.",
    score: 0.88,
    why: "Open loop flagged by retriever + H-MEM",
    ts: "2026-06-10T14:22:00Z",
  },
  {
    id: "mem_coffee",
    text: "Coffee with Omar is usually at Desautels or Notman House.",
    score: 0.64,
    why: "Calendar pattern over last 6 months",
    ts: "2026-05-01T09:00:00Z",
  },
];

export const omarLedger: LedgerEvent[] = [
  {
    id: "led_1",
    kind: "observation",
    summary: "Merged WhatsApp + Gmail handles into entity omar-haddad",
    ts: "2026-10-15T12:01:00Z",
  },
  {
    id: "led_2",
    kind: "conflict",
    summary: "city: Montreal (contact) vs Toronto (whatsapp)",
    ts: "2026-10-15T12:01:12Z",
  },
  {
    id: "led_3",
    kind: "memory_write",
    summary: "Stored open-loop memory about Rachel Goldberg intro",
    ts: "2026-10-15T12:02:04Z",
  },
];
