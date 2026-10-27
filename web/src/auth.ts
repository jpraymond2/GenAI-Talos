/** API bearer token for /api/* and /ws/trace?token=. */

const STORAGE_KEY = "recall_api_token";

export function getApiToken(): string {
  const fromEnv = String(import.meta.env.VITE_RECALL_API_TOKEN ?? "").trim();
  if (fromEnv) return fromEnv;
  try {
    return String(localStorage.getItem(STORAGE_KEY) ?? "").trim();
  } catch {
    return "";
  }
}

export function setApiToken(token: string): void {
  try {
    localStorage.setItem(STORAGE_KEY, token.trim());
  } catch {
    /* private mode / blocked storage */
  }
}

let localTokenPromise: Promise<string> | null = null;

/** Fetch the sample-mode token from GET /api/local-token (loopback only). */
export async function fetchLocalToken(): Promise<string> {
  if (localTokenPromise) return localTokenPromise;
  localTokenPromise = (async () => {
    try {
      const res = await fetch("/api/local-token");
      if (!res.ok) return "";
      const body = (await res.json()) as { token?: string };
      const token = String(body.token ?? "").trim();
      if (token) setApiToken(token);
      return token;
    } catch {
      return "";
    }
  })();
  return localTokenPromise;
}

/** Resolve a token: env / storage, then local-token, then optional prompt. */
export async function ensureApiTokenAsync(): Promise<string> {
  let token = getApiToken();
  if (token) return token;
  token = await fetchLocalToken();
  if (token) return token;
  if (typeof window === "undefined") return "";
  const entered = window.prompt(
    "Enter RECALL_API_TOKEN (from the PC .env). Stored in localStorage.",
  );
  if (entered && entered.trim()) {
    setApiToken(entered.trim());
    token = entered.trim();
  }
  return token;
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
