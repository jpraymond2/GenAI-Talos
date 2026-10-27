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

/** Token for requests. The login page sets it; there is no browser prompt anymore. */
export function ensureApiToken(): string {
  return getApiToken();
}

export function authHeaders(extra: Record<string, string> = {}): Record<string, string> {
  const token = getApiToken();
  const headers: Record<string, string> = { ...extra };
  if (token) headers.Authorization = `Bearer ${token}`;
  return headers;
}
