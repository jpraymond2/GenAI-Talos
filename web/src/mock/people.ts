import type { DeviceStatus, MacContact, PersonCard } from "../types";

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
