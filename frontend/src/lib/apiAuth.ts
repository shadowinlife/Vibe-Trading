import { safeGet, safeRemove, safeSet } from "@/lib/storage";

/**
 * Session token minted by `POST /auth/login` or `POST /auth/register`.
 *
 * Stored in localStorage (not a cookie) so the CSRF surface stays zero: a
 * cross-site script cannot read another origin's storage, and `Authorization`
 * is not a CORS-safelisted header. The trade-off — XSS can read it — is
 * bounded by the strict CSP (`script-src 'self'`, no `unsafe-inline`).
 */
const SESSION_TOKEN_KEY = "vibe_trading_session_token";

/**
 * Legacy shared `API_AUTH_KEY` value, entered once per browser by remote/LAN
 * users while user auth is OFF. Kept byte-compatible so that world is
 * unchanged (D19); `clearLegacyApiAuthKey()` drops it once `GET /auth/mode`
 * reports `user_auth: true`, where the shared key is meaningless to the new
 * system and — because the backend still honours `API_AUTH_KEY` as a
 * break-glass secret — an undesirable dormant credential in localStorage.
 */
const LEGACY_API_KEY_STORAGE_KEY = "vibe_trading_api_auth_key";

export function getSessionToken(): string {
  return safeGet(SESSION_TOKEN_KEY) || "";
}

export function setSessionToken(value: string): void {
  const trimmed = value.trim();
  if (trimmed) {
    safeSet(SESSION_TOKEN_KEY, trimmed);
  } else {
    safeRemove(SESSION_TOKEN_KEY);
  }
}

export function clearSessionToken(): void {
  safeRemove(SESSION_TOKEN_KEY);
}

export function getApiAuthKey(): string {
  return safeGet(LEGACY_API_KEY_STORAGE_KEY) || "";
}

export function setApiAuthKey(value: string): void {
  const trimmed = value.trim();
  if (trimmed) {
    safeSet(LEGACY_API_KEY_STORAGE_KEY, trimmed);
  } else {
    safeRemove(LEGACY_API_KEY_STORAGE_KEY);
  }
}

export function clearLegacyApiAuthKey(): void {
  safeRemove(LEGACY_API_KEY_STORAGE_KEY);
}

/**
 * The credential sent as `Authorization: Bearer <token>`: the session token
 * when one exists, otherwise the legacy shared key (flag-off deployments,
 * where the backend's key-first branch answers it).
 */
function getAuthToken(): string {
  return getSessionToken() || getApiAuthKey();
}

/** Whether any credential is stored — decides if an SSE ticket round-trip is needed. */
export function hasAuthToken(): boolean {
  return getAuthToken().length > 0;
}

export function authHeaders(): Record<string, string> {
  const token = getAuthToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

/**
 * A non-OK reply from `POST /auth/sse-ticket`, carrying the HTTP status so
 * callers can distinguish an expired session (401/403 → re-auth) from a
 * transient failure (→ retry the connection).
 */
export class AuthTicketError extends Error {
  status: number;

  constructor(status: number) {
    super(`Failed to obtain SSE ticket (HTTP ${status})`);
    this.name = "AuthTicketError";
    this.status = status;
  }

  /**
   * A 401 means the stored credential is refused and the session is gone.
   * A 403 (cross-site rejection, or an admin gate on another route) is a
   * permission verdict and must never be read as session death.
   */
  get unauthorized(): boolean {
    return this.status === 401;
  }
}

/**
 * Append a short-lived, single-use SSE ticket to an EventSource URL.
 *
 * A browser `EventSource` cannot set an `Authorization` header, so we exchange
 * the stored credential (sent in a header on this POST) for a one-shot ticket
 * via `POST /auth/sse-ticket`, then open the stream with `?ticket=`. This keeps
 * the long-lived credential out of URLs, browser history, and proxy/access logs.
 *
 * When no credential is stored the backend is in loopback dev mode (auth
 * bypassed), so the URL is returned unchanged and no ticket round-trip is made.
 * Tickets are single-use: every connect/reconnect must mint a fresh one, so
 * callers invoke this per connection attempt rather than caching the result.
 */
export async function withAuthTicket(url: string): Promise<string> {
  const token = getAuthToken();
  if (!token) return url;
  const res = await fetch("/auth/sse-ticket", {
    method: "POST",
    headers: authHeaders(),
  });
  if (!res.ok) {
    throw new AuthTicketError(res.status);
  }
  const data: unknown = await res.json();
  const ticket = (data as { ticket?: unknown } | null)?.ticket;
  if (typeof ticket !== "string" || !ticket) {
    throw new Error("SSE ticket response missing ticket");
  }
  const sep = url.includes("?") ? "&" : "?";
  return `${url}${sep}ticket=${encodeURIComponent(ticket)}`;
}