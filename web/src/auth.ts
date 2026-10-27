/** Dashboard session: API bearer token + who's signed in (localStorage). */

import type { SessionUser } from "./types";

const TOKEN_KEY = "recall_api_token";
const USER_KEY = "recall_user";

export function getApiToken(): string {
  const fromEnv = String(import.meta.env.VITE_RECALL_API_TOKEN ?? "").trim();
  if (fromEnv) return fromEnv;
  try {
    return String(localStorage.getItem(TOKEN_KEY) ?? "").trim();
  } catch {
    return "";
  }
}

export function setApiToken(token: string): void {
  try {
    localStorage.setItem(TOKEN_KEY, token.trim());
  } catch {
    /* private mode / blocked storage */
  }
}

export function getUser(): SessionUser | null {
  try {
    const raw = localStorage.getItem(USER_KEY);
    return raw ? (JSON.parse(raw) as SessionUser) : null;
  } catch {
    return null;
  }
}

export function setUser(user: SessionUser): void {
  try {
    localStorage.setItem(USER_KEY, JSON.stringify(user));
  } catch {
    /* ignore */
  }
}

export function logout(): void {
  try {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
  } catch {
    /* ignore */
  }
}

let localTokenPromise: Promise<LocalTokenResult> | null = null;

export interface LocalTokenResult {
  token: string;
  name: string;
  email: string;
}

/** Fetch the sample-mode token (+ owner) from GET /api/local-token (loopback only). */
export async function fetchLocalToken(): Promise<LocalTokenResult> {
  if (localTokenPromise) return localTokenPromise;
  localTokenPromise = (async () => {
    const empty = { token: "", name: "", email: "" };
    try {
      const res = await fetch("/api/local-token");
      if (!res.ok) return empty;
      const body = (await res.json()) as {
        token?: string;
        name?: string;
        email?: string;
      };
      const token = String(body.token ?? "").trim();
      if (token) setApiToken(token);
      return {
        token,
        name: String(body.name ?? "").trim(),
        email: String(body.email ?? "").trim(),
      };
    } catch {
      return empty;
    }
  })();
  return localTokenPromise;
}

/** Resolve a token: env / storage, then local-token (no browser prompt — login page owns that). */
export async function ensureApiTokenAsync(): Promise<string> {
  let token = getApiToken();
  if (token) return token;
  const local = await fetchLocalToken();
  return local.token;
}

/** Sync helper used by WebSocket setup; prefer ensureApiTokenAsync for fetches. */
export function ensureApiToken(): string {
  return getApiToken();
}

export function authHeaders(extra: Record<string, string> = {}): Record<string, string> {
  const token = getApiToken();
  const headers: Record<string, string> = { ...extra };
  if (token) headers.Authorization = `Bearer ${token}`;
  return headers;
}

export async function authHeadersAsync(
  extra: Record<string, string> = {},
): Promise<Record<string, string>> {
  const token = await ensureApiTokenAsync();
  const headers: Record<string, string> = { ...extra };
  if (token) headers.Authorization = `Bearer ${token}`;
  return headers;
}
