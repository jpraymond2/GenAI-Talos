/** Login, contact cards, and Mac pulls — talks to recall/server/workspace.py (mock when offline). */

import { shouldMock } from "./api";
import { authHeaders, setApiToken, setUser } from "./auth";
import { mockConnections, mockDevice, mockMacContacts, mockPeople, mockUpcoming } from "./mock/people";
import type { CalendarEvent, ConnectionsState, DeviceStatus, MacContact, PersonCard, PersonEdit, PullJob, PullParams, SessionUser } from "./types";

let mockStore: PersonCard[] = structuredClone(mockPeople);
let mockPull: PullJob | null = null;
let mockGoogle = false;

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = `${res.status}`;
    try {
      const body = (await res.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      /* keep status */
    }
    throw new Error(detail);
  }
  return (await res.json()) as T;
}

export async function login(email: string, password: string): Promise<SessionUser> {
  if (await shouldMock()) {
    const user = { name: email ? email.split("@")[0] : "Demo", email };
    setApiToken("demo");
    setUser(user);
    return user;
  }
  const res = await fetch("/api/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  const data = await json<{ token: string; name: string; email: string }>(res);
  setApiToken(data.token);
  const user = { name: data.name, email: data.email };
  setUser(user);
  return user;
}

/** "Skip sign-in": demo / judging access (server allows it on synthetic data). */
export async function loginAsGuest(): Promise<SessionUser> {
  if (await shouldMock()) {
    const user = { name: "Demo", email: "" };
    setApiToken("demo");
    setUser(user);
    return user;
  }
  const res = await fetch("/api/login/guest", { method: "POST" });
  const data = await json<{ token: string; name: string; email: string }>(res);
  setApiToken(data.token);
  const user = { name: data.name, email: data.email };
  setUser(user);
  return user;
}

export async function fetchPeople(): Promise<PersonCard[]> {
  if (await shouldMock()) return structuredClone(mockStore);
  const res = await fetch("/api/people", { headers: authHeaders() });
  if (res.status === 404) return [];
  return (await json<{ people: PersonCard[] }>(res)).people;
}

export async function fetchPersonCard(entityId: string): Promise<PersonCard | null> {
  if (await shouldMock()) {
    return structuredClone(mockStore.find((p) => p.entity_id === entityId) ?? mockStore[0] ?? null);
  }
  const res = await fetch(`/api/people/${encodeURIComponent(entityId)}`, { headers: authHeaders() });
  if (!res.ok) return null;
  return (await res.json()) as PersonCard;
}

export async function editPerson(entityId: string, edit: PersonEdit): Promise<PersonCard> {
  if (await shouldMock()) {
    const idx = mockStore.findIndex((p) => p.entity_id === entityId);
    const cur = mockStore[idx];
    const next: PersonCard = {
      ...cur,
      name: edit.name ?? cur.name,
      org: edit.org ?? cur.org,
      title: edit.title ?? cur.title,
      city: edit.city ?? cur.city,
      notes: edit.notes ?? cur.notes,
      other_names: [...cur.other_names.filter((n) => !(edit.remove_aliases ?? []).includes(n)), ...(edit.add_aliases ?? [])],
      edited_fields: Array.from(new Set([...cur.edited_fields, ...Object.keys(edit).filter((k) => k !== "add_aliases" && k !== "remove_aliases")])),
    };
    mockStore[idx] = next;
    return structuredClone(next);
  }
  const res = await fetch(`/api/people/${encodeURIComponent(entityId)}`, {
    method: "PATCH",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(edit),
  });
  return json<PersonCard>(res);
}

export async function mergePeople(entityId: string, otherId: string, samePerson = true): Promise<PersonCard | null> {
  if (await shouldMock()) {
    const a = mockStore.find((p) => p.entity_id === entityId);
    const b = mockStore.find((p) => p.entity_id === otherId);
    if (!a || !b) return null;
    if (samePerson) {
      a.emails = Array.from(new Set([...a.emails, ...b.emails]));
      a.phones = Array.from(new Set([...a.phones, ...b.phones]));
      a.other_names = Array.from(new Set([...a.other_names, b.name, ...b.other_names]));
      a.mentions += b.mentions;
      a.merged_from += 1;
      mockStore = mockStore.filter((p) => p.entity_id !== otherId);
    }
    return structuredClone(a);
  }
  const res = await fetch(`/api/people/${encodeURIComponent(entityId)}/merge`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ other_id: otherId, same_person: samePerson }),
  });
  return (await json<{ card: PersonCard | null }>(res)).card;
}

export async function fetchDevice(): Promise<DeviceStatus> {
  if (await shouldMock()) return { ...mockDevice, last_seen: new Date().toISOString() };
  return json<DeviceStatus>(await fetch("/api/mac/device", { headers: authHeaders() }));
}

export async function fetchMacContacts(): Promise<MacContact[]> {
  if (await shouldMock()) return structuredClone(mockMacContacts);
  return (await json<{ contacts: MacContact[] }>(await fetch("/api/mac/contacts", { headers: authHeaders() }))).contacts;
}

function mockAdvance(): PullJob | null {
  if (!mockPull) return null;
  const age = Date.now() - new Date(mockPull.created_at).getTime();
  const steps: [number, PullJob["status"], string][] = [
    [1200, "queued", "waiting for your Mac"],
    [3200, "running", "reading messages on this Mac"],
    [5200, "uploading", "uploading"],
    [7000, "building", "sorting contacts"],
  ];
  const step = steps.find(([ms]) => age < ms);
  const n = Math.round(40 * (mockPull.params.days / 30) * (mockPull.params.contacts === "all" ? 1 : 0.3));
  mockPull = {
    ...mockPull,
    status: step ? step[1] : "ready",
    progress: { stage: step ? step[2] : "done", imessage_messages: n * 9, whatsapp_messages: mockPull.params.sources.includes("whatsapp") ? n * 4 : 0, contacts: mockStore.length },
  };
  return structuredClone(mockPull);
}

export async function startPull(params: PullParams): Promise<PullJob> {
  if (await shouldMock()) {
    const now = new Date().toISOString();
    mockPull = { id: `pull_demo_${Date.now()}`, status: "queued", created_at: now, updated_at: now, params, progress: {}, error: "" };
    return structuredClone(mockPull);
  }
  const res = await fetch("/api/mac/pulls", {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(params),
  });
  return (await json<{ pull: PullJob }>(res)).pull;
}

export async function latestPull(): Promise<{ pull: PullJob | null; device: DeviceStatus }> {
  if (await shouldMock()) return { pull: mockAdvance(), device: { ...mockDevice, last_seen: new Date().toISOString() } };
  return json(await fetch("/api/mac/pulls/latest", { headers: authHeaders() }));
}

export async function fetchConnections(): Promise<ConnectionsState> {
  if (await shouldMock()) return mockConnections({ ...mockDevice, last_seen: new Date().toISOString() }, mockGoogle);
  return json<ConnectionsState>(await fetch("/api/connections", { headers: authHeaders() }));
}

/** Starts Google sign-in (Gmail + Calendar). Live: navigates to Google and comes back to /?connected=google. */
export async function connectGoogle(): Promise<"redirecting" | "connected"> {
  if (await shouldMock()) {
    await new Promise((r) => window.setTimeout(r, 1200));
    mockGoogle = true;
    return "connected";
  }
  const { url } = await json<{ url: string }>(await fetch("/api/connect/google/start", { headers: authHeaders() }));
  window.location.assign(url);
  return "redirecting";
}

export async function disconnectGoogle(): Promise<void> {
  if (await shouldMock()) {
    mockGoogle = false;
    return;
  }
  await json(await fetch("/api/connections/google", { method: "DELETE", headers: authHeaders() }));
}

export async function fetchUpcoming(days = 14): Promise<{ now: string; events: CalendarEvent[] }> {
  if (await shouldMock()) return { now: new Date().toISOString(), events: mockUpcoming() };
  const res = await fetch(`/api/calendar/upcoming?days=${days}`, { headers: authHeaders() });
  if (res.status === 404) return { now: new Date().toISOString(), events: [] };
  return json(res);
}
