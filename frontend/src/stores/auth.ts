import { create } from "zustand";
import {
  clearLegacyApiAuthKey,
  clearSessionToken,
  getSessionToken,
  setSessionToken,
} from "@/lib/apiAuth";

/** Role literals as returned by the backend (`GET /auth/me`, login, register). */
export type AuthRole = "user" | "admin";

export interface AuthSession {
  token: string;
  username: string;
  role: AuthRole;
  displayName: string | null;
}

/** Shape the store normalizes incoming payloads through, before they are trusted. */
export interface AuthSessionInput {
  token: string;
  username: string;
  role: string;
  displayName: string | null;
}

interface AuthState {
  /**
   * Backend capability flag from `GET /auth/mode` (D19). When false the app
   * must behave exactly as it did before user auth existed: no guard, no
   * session-expiry handling, and the legacy Settings API-key section stays.
   */
  userAuth: boolean;
  /** True once `/auth/mode` has resolved — however it resolved. */
  modeLoaded: boolean;
  token: string | null;
  username: string | null;
  role: AuthRole | null;
  displayName: string | null;

  setUserAuth: (userAuth: boolean) => void;
  setSession: (session: AuthSessionInput) => void;
  setIdentity: (identity: { username: string; role: string; displayName: string | null }) => void;
  clearSession: () => void;
}

/** Unknown role strings degrade to the least-privileged role, never to admin. */
function normalizeRole(role: string): AuthRole {
  return role === "admin" ? "admin" : "user";
}

export const useAuthStore = create<AuthState>((set) => ({
  userAuth: false,
  modeLoaded: false,
  token: getSessionToken() || null,
  username: null,
  role: null,
  displayName: null,

  setUserAuth: (userAuth) => {
    if (userAuth) {
      // The shared key has no meaning (and is a dormant god-mode credential)
      // once the backend authenticates real users; see apiAuth.ts.
      clearLegacyApiAuthKey();
    }
    set({ userAuth, modeLoaded: true });
  },

  setSession: (session) => {
    setSessionToken(session.token);
    set({
      token: session.token,
      username: session.username,
      role: normalizeRole(session.role),
      displayName: session.displayName,
    });
  },

  setIdentity: (identity) =>
    set({
      username: identity.username,
      role: normalizeRole(identity.role),
      displayName: identity.displayName,
    }),

  clearSession: () => {
    clearSessionToken();
    set({ token: null, username: null, role: null, displayName: null });
  },
}));

/**
 * Re-auth channel shared by the API layer and the SSE hook: a 401/403 that
 * invalidates the session clears the stored token, which unmounts the guarded
 * routes and lands the user on `/login`.
 *
 * Deliberately inert unless user auth is on AND a session actually exists, so
 * flag-off deployments keep today's toast-only behaviour (D19) and a failed
 * login attempt (no session yet) cannot masquerade as an expired one. Returns
 * whether it acted, so callers can branch on "the session really is gone"
 * instead of guessing from the status code.
 */
export function expireSession(): boolean {
  const { userAuth, token } = useAuthStore.getState();
  if (!userAuth || !token) return false;
  useAuthStore.getState().clearSession();
  return true;
}