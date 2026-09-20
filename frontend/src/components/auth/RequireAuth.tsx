import { useEffect, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Navigate } from "react-router";
import { Loader2 } from "lucide-react";
import { api, isSessionExpiredError } from "@/lib/api";
import { useAuthStore } from "@/stores/auth";

function AuthPending() {
  const { t } = useTranslation();
  return (
    <div className="flex h-screen items-center justify-center bg-background text-sm text-muted-foreground">
      <Loader2 className="me-2 h-4 w-4 animate-spin" aria-hidden="true" />
      {t("auth.checkingSession")}
    </div>
  );
}

/**
 * Route guard for the whole application shell.
 *
 * With `user_auth` off it is fully bypassed — flag-off deployments have no
 * session concept and must behave exactly as before (D19).
 *
 * With it on, a stored token is not a session: it is validated against
 * `GET /auth/me` on mount, and nothing renders until that answers, so a stale
 * token can neither flash the app nor leave it half-mounted. A rejected token
 * is already cleared by the API layer; rendering the redirect here is what
 * takes the user to the login page (after which the store's token is null, so
 * the redirect is a render, not a side effect).
 */
export function RequireAuth({ children }: { children: ReactNode }) {
  const userAuth = useAuthStore((state) => state.userAuth);
  const modeLoaded = useAuthStore((state) => state.modeLoaded);
  const token = useAuthStore((state) => state.token);
  const [checkedToken, setCheckedToken] = useState<string | null>(null);

  useEffect(() => {
    if (!userAuth || !token) return;
    let alive = true;
    api.auth
      .me()
      .then((me) => {
        if (!alive) return;
        useAuthStore.getState().setIdentity({
          username: me.username,
          role: me.role,
          displayName: me.display_name,
        });
        setCheckedToken(token);
      })
      .catch((error: unknown) => {
        if (!alive) return;
        if (isSessionExpiredError(error)) {
          // The guard owns this verdict: a refused session must never render
          // the app, even if the rejection bypassed the API error handler.
          useAuthStore.getState().clearSession();
          return;
        }
        // A 403 is a permission verdict and a transport failure is neither —
        // fail open instead of ending a live session over an unrelated rejection.
        setCheckedToken(token);
      });
    return () => {
      alive = false;
    };
  }, [userAuth, token]);

  if (!modeLoaded) return <AuthPending />;
  if (!userAuth) return <>{children}</>;
  if (!token) return <Navigate to="/login" replace />;
  if (checkedToken !== token) return <AuthPending />;
  return <>{children}</>;
}

/** Route guard for operator surfaces (Settings): admins only, when user auth is on. */
export function RequireAdmin({ children }: { children: ReactNode }) {
  const userAuth = useAuthStore((state) => state.userAuth);
  const role = useAuthStore((state) => state.role);

  if (userAuth && role !== "admin") return <Navigate to="/" replace />;
  return <>{children}</>;
}