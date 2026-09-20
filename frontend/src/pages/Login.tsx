import { useId, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Navigate, useNavigate } from "react-router";
import { KeyRound, Loader2, ShieldAlert, UserPlus } from "lucide-react";
import { BrandMark } from "@/components/common/BrandMark";
import { ApiError, api } from "@/lib/api";
import { cn } from "@/lib/utils";
import { useAuthStore } from "@/stores/auth";

type AuthMode = "login" | "register";

const USERNAME_PATTERN = /^[A-Za-z0-9_-]{3,32}$/;
const MIN_PASSWORD_LENGTH = 8;

const fieldClass =
  "w-full rounded-md border bg-background px-3 py-2 text-sm outline-none transition focus:border-primary focus:ring-2 focus:ring-primary/20 disabled:cursor-not-allowed disabled:opacity-60";
const labelClass = "grid gap-2 text-sm font-medium";

/** A plain-HTTP entry point puts the password on the wire in clear text (§4.1). */
function isInsecureTransport(): boolean {
  if (typeof window === "undefined" || window.location.protocol !== "http:") return false;
  return !["localhost", "127.0.0.1", "[::1]", "::1"].includes(window.location.hostname);
}

export function Login() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const fieldPrefix = useId();
  const userAuth = useAuthStore((state) => state.userAuth);
  const modeLoaded = useAuthStore((state) => state.modeLoaded);
  const token = useAuthStore((state) => state.token);

  const [mode, setMode] = useState<AuthMode>("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [inviteCode, setInviteCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [insecureTransport] = useState(isInsecureTransport);

  // Unreachable without user auth, and a signed-in visitor is already past this
  // form — a rejected re-login would otherwise end their live session.
  if (modeLoaded && (!userAuth || token)) return <Navigate to="/" replace />;

  const usernameId = `${fieldPrefix}-username`;
  const passwordId = `${fieldPrefix}-password`;
  const confirmId = `${fieldPrefix}-confirm`;
  const inviteId = `${fieldPrefix}-invite`;
  const ActionIcon = mode === "login" ? KeyRound : UserPlus;

  const loginError = (thrown: unknown): string =>
    thrown instanceof Error ? thrown.message : t("auth.requestFailed");

  const registerError = (thrown: unknown): string => {
    if (thrown instanceof ApiError) {
      if (thrown.status === 409) return t("auth.usernameTaken");
      if (thrown.status === 403) return t("auth.registerDisabled");
    }
    return loginError(thrown);
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (submitting) return;
    const name = username.trim();
    if (!USERNAME_PATTERN.test(name)) {
      setError(t("auth.usernameInvalid"));
      return;
    }
    if (password.length < MIN_PASSWORD_LENGTH) {
      setError(t("auth.passwordTooShort", { min: MIN_PASSWORD_LENGTH }));
      return;
    }
    if (mode === "register" && password !== confirmPassword) {
      setError(t("auth.passwordMismatch"));
      return;
    }

    setSubmitting(true);
    setError(null);
    try {
      const session = mode === "login"
        ? await api.auth.login({ username: name, password })
        : await api.auth.register({
            username: name,
            password,
            invite_code: inviteCode.trim(),
          });
      useAuthStore.getState().setSession({
        token: session.token,
        username: session.username,
        role: session.role,
        displayName: session.display_name,
      });
      navigate("/", { replace: true });
    } catch (thrown: unknown) {
      setError(mode === "register" ? registerError(thrown) : loginError(thrown));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <main className="flex min-h-screen items-center justify-center bg-background px-4 py-10 text-foreground">
      <div className="w-full max-w-sm space-y-6">
        <div className="flex flex-col items-center gap-3 text-center">
          <BrandMark className="h-10 w-10" />
          <div className="space-y-1">
            <h1 className="text-xl font-semibold tracking-tight">Vibe-Trading</h1>
            <p className="text-sm text-muted-foreground">{t("auth.subtitle")}</p>
          </div>
        </div>

        {insecureTransport && (
          <p
            role="alert"
            className="flex items-start gap-2 rounded-md border border-warning/40 bg-warning/10 px-3 py-2 text-xs text-warning"
          >
            <ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
            {t("auth.insecureTransport")}
          </p>
        )}

        <div className="rounded-lg border bg-card p-5 shadow-sm">
          <div
            role="tablist"
            aria-label={t("auth.title")}
            className="mb-5 grid grid-cols-2 gap-1 rounded-md bg-muted/50 p-1"
          >
            {(["login", "register"] as const).map((tab) => (
              <button
                key={tab}
                type="button"
                role="tab"
                id={`${fieldPrefix}-${tab}-tab`}
                aria-selected={mode === tab}
                onClick={() => {
                  setMode(tab);
                  setError(null);
                }}
                className={cn(
                  "rounded px-3 py-1.5 text-sm transition-colors",
                  mode === tab
                    ? "bg-background font-medium text-foreground shadow-sm"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                {tab === "login" ? t("auth.tabSignIn") : t("auth.tabRegister")}
              </button>
            ))}
          </div>

          <form
            onSubmit={submit}
            role="tabpanel"
            aria-labelledby={`${fieldPrefix}-${mode}-tab`}
            className="grid gap-4"
          >
            <label className={labelClass} htmlFor={usernameId}>
              {t("auth.username")}
              <input
                id={usernameId}
                name="username"
                value={username}
                onChange={(event) => setUsername(event.target.value)}
                className={fieldClass}
                autoComplete="username"
                autoCapitalize="none"
                autoFocus
                required
              />
            </label>

            <label className={labelClass} htmlFor={passwordId}>
              {t("auth.password")}
              <input
                id={passwordId}
                name="password"
                type="password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                className={fieldClass}
                autoComplete={mode === "login" ? "current-password" : "new-password"}
                required
              />
            </label>

            {mode === "register" && (
              <>
                <label className={labelClass} htmlFor={confirmId}>
                  {t("auth.confirmPassword")}
                  <input
                    id={confirmId}
                    name="confirm-password"
                    type="password"
                    value={confirmPassword}
                    onChange={(event) => setConfirmPassword(event.target.value)}
                    className={fieldClass}
                    autoComplete="new-password"
                    required
                  />
                </label>

                <label className={labelClass} htmlFor={inviteId}>
                  {t("auth.inviteCode")}
                  <input
                    id={inviteId}
                    name="invite-code"
                    value={inviteCode}
                    onChange={(event) => setInviteCode(event.target.value)}
                    className={cn(fieldClass, "font-mono")}
                    autoComplete="off"
                    required
                  />
                </label>
                <p className="-mt-2 text-xs text-muted-foreground">{t("auth.inviteHint")}</p>
              </>
            )}

            {error && (
              <p role="alert" className="rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-xs text-danger">
                {error}
              </p>
            )}

            <button
              type="submit"
              disabled={submitting}
              className="inline-flex items-center justify-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {submitting ? (
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              ) : (
                <ActionIcon className="h-4 w-4" aria-hidden="true" />
              )}
              {submitting
                ? mode === "login" ? t("auth.signingIn") : t("auth.registering")
                : mode === "login" ? t("auth.signIn") : t("auth.register")}
            </button>
          </form>
        </div>
      </div>
    </main>
  );
}