import type { CalendarEvent, ConnectionsState, DeviceStatus, MacContact, PersonCard, SourceConnection } from "../types";

/** Demo contact cards (the fictional Alex Martin world) for VITE_MOCK / no backend. */
function card(p: Partial<PersonCard> & Pick<PersonCard, "entity_id" | "name">): PersonCard {
  return {
    other_names: [], emails: [], phones: [], org: "", title: "", city: "", notes: "", edited_fields: [],
    relationship: "", first_seen_at: "2025-09-02T14:00:00Z", last_seen_at: "2026-10-20T18:00:00Z",
    mentions: 10, confidence: 0.9, sources: ["imessage"], merged_from: 0, ...p,
  };
}

export const mockPeople: PersonCard[] = [
  card({ entity_id: "karim-haddad", name: "Karim Haddad", other_names: ["Karim H.", "Karim 🇱🇧"], emails: ["karim@ledgerly.io", "kh@ledgerly.io"],
    phones: ["+15145550142"], org: "Ledgerly", title: "Founder", city: "Montreal", relationship: "mixed", mentions: 96,
    sources: ["contact", "gmail", "imessage", "whatsapp", "calendar"], merged_from: 2, last_seen_at: "2026-10-22T16:48:00Z" }),
  card({ entity_id: "karim-saleh", name: "Karim Saleh", emails: ["karim.saleh@mcgill.ca", "karim@pwc.com"], phones: ["+15145550143"],
    org: "PwC Advisory", title: "Incoming Associate", city: "Montreal", relationship: "social", mentions: 31, sources: ["contact", "gmail", "imessage"] }),
  card({ entity_id: "lina-chen", name: "Lina Chen", emails: ["lina.chen@northbridge.vc", "lchen@northbridge.vc"], org: "Northbridge Ventures",
    title: "Principal", city: "Toronto", relationship: "professional", mentions: 18, sources: ["gmail", "calendar"] }),
  card({ entity_id: "sophie-tremblay", name: "Sophie Tremblay", emails: ["sophie.tremblay@mail.mcgill.ca"], phones: ["+15145550111"],
    org: "McGill University", city: "Montreal", relationship: "social", mentions: 54, sources: ["contact", "imessage", "gmail"] }),
  card({ entity_id: "maya-martin", name: "Maya Martin", phones: ["+16135550177"], city: "Ottawa", relationship: "family", mentions: 77,
    sources: ["whatsapp", "imessage"] }),
  card({ entity_id: "emma-rossi", name: "Emma Rossi", emails: ["emma.rossi@mail.mcgill.ca"], org: "McGill University",
    relationship: "social", mentions: 23, sources: ["gmail"] }),
  card({ entity_id: "marcus-wei", name: "Marcus Wei", emails: ["marcus@northstar-recruit.com"], org: "Northstar Recruiting", title: "Recruiter",
    relationship: "professional", mentions: 9, sources: ["gmail", "calendar"] }),
  card({ entity_id: "diego-alvarez", name: "Diego Alvarez", phones: ["+15145550133"], relationship: "social", mentions: 15, sources: ["imessage"] }),
];

export const mockMacContacts: MacContact[] = mockPeople.map((p, i) => ({
  contact_id: `ab-demo-${i}`, name: p.name, organization: p.org, phones: p.phones, emails: p.emails,
}));

export const mockDevice: DeviceStatus = {
  connected: true, online: true, last_seen: new Date().toISOString(), device_id: "demo-mac", agent_version: "0.1.0", hostname: "Hassan's MacBook",
};

/** Demo meetings, always in the near future so the calendar never looks empty. */
export function mockUpcoming(): CalendarEvent[] {
  const at = (days: number, hour: number, min = 0) => {
    const d = new Date();
    d.setDate(d.getDate() + days);
    d.setHours(hour, min, 0, 0);
    return d;
  };
  const ev = (id: string, title: string, start: Date, mins: number, location: string, people: [string, string][], extra: string[] = []): CalendarEvent => ({
    id, title, start: start.toISOString(), end: new Date(start.getTime() + mins * 60000).toISOString(), location, source_ref: `calendar:${id}`,
    attendees: [...people.map(([entity_id, name]) => ({ email: "", entity_id, name })), ...extra.map((email) => ({ email, entity_id: null, name: "" }))],
  });
  return [
    ev("d1", "Coffee w/ Karim", new Date(Math.ceil((Date.now() + 40 * 60000) / 900000) * 900000), 45, "Café Olimpico, Mile End", [["karim-haddad", "Karim Haddad"]]),
    ev("d2", "Northbridge intro call", at(1, 10), 30, "Google Meet", [["lina-chen", "Lina Chen"]]),
    ev("d3", "Study group — COMP 551", at(1, 15), 90, "Trottier 2100", [["sophie-tremblay", "Sophie Tremblay"], ["emma-rossi", "Emma Rossi"]]),
    ev("d4", "Recruiter screen — Northstar", at(2, 11, 30), 30, "Phone", [["marcus-wei", "Marcus Wei"]]),
    ev("d5", "Dinner with Maya", at(3, 19), 120, "Ottawa", [["maya-martin", "Maya Martin"]]),
    ev("d6", "PwC case prep", at(5, 9), 60, "Bronfman 151", [["karim-saleh", "Karim Saleh"]], ["coach@pwc.com"]),
  ];
}

export function mockConnections(device: DeviceStatus, googleConnected: boolean): ConnectionsState {
  const sources: SourceConnection[] = [
    { id: "imessage", name: "iMessage", via: "mac", status: "connected", detail: "1,284 messages", items: 1284, last_sync: new Date(Date.now() - 3600e3).toISOString(), action: "pull" },
    { id: "whatsapp", name: "WhatsApp", via: "mac", status: "ready", detail: "Mac connected — pull to add", items: null, last_sync: null, action: "pull" },
    { id: "contacts", name: "Address book", via: "mac", status: "connected", detail: "212 cards", items: 212, last_sync: new Date(Date.now() - 3600e3).toISOString(), action: "pull" },
    googleConnected
      ? { id: "gmail", name: "Gmail", via: "google", status: "connected", detail: "signed in — first sync running", items: null, last_sync: new Date().toISOString(), action: "reconnect" }
      : { id: "gmail", name: "Gmail", via: "google", status: "off", detail: "Sign in with Google", items: null, last_sync: null, action: "connect" },
    googleConnected
      ? { id: "calendar", name: "Google Calendar", via: "google", status: "connected", detail: "6 upcoming meetings", items: 6, last_sync: new Date().toISOString(), action: "reconnect" }
      : { id: "calendar", name: "Google Calendar", via: "google", status: "off", detail: "Sign in with Google", items: null, last_sync: null, action: "connect" },
  ];
  return { sources, mac: device, google: { configured: true, connected: googleConnected }, connected: sources.filter((s) => s.status === "connected").length, total: sources.length };
}
