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

/** Prompt once when live mode needs a token and none is configured. */
export function ensureApiToken(): string {
  let token = getApiToken();
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

export function authHeaders(extra: Record<string, string> = {}): Record<string, string> {
  const token = ensureApiToken();
  const headers: Record<string, string> = { ...extra };
  if (token) headers.Authorization = `Bearer ${token}`;
  return headers;
}
