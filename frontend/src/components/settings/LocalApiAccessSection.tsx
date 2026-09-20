import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { KeyRound, Save } from "lucide-react";
import { toast } from "sonner";
import { getApiAuthKey, setApiAuthKey } from "@/lib/apiAuth";

const fieldClass =
  "w-full rounded-md border bg-background px-3 py-2 text-sm outline-none transition focus:border-primary focus:ring-2 focus:ring-primary/20 disabled:cursor-not-allowed disabled:opacity-60";
const labelClass = "text-sm font-medium";

/**
 * Shared-key entry for remote/LAN Web UI deployments (GHSA-7wgj companion UX).
 *
 * Rendered only while the backend reports `user_auth: false`: with real
 * accounts nobody needs a server API key, and offering the field anyway would
 * invite exactly the credential confusion this whole change removes (D19).
 */
export function LocalApiAccessSection() {
  const { t } = useTranslation();
  const [localApiKey, setLocalApiKeyState] = useState(() => getApiAuthKey());

  const submitLocalApiKey = (event: FormEvent) => {
    event.preventDefault();
    setApiAuthKey(localApiKey);
    toast.success(t("settings.localApiKeySaved"));
    window.location.reload();
  };

  return (
    <form onSubmit={submitLocalApiKey} className="rounded-lg border bg-card p-5 shadow-sm">
      <div className="mb-4 space-y-1">
        <div className="flex items-center gap-2">
          <KeyRound className="h-4 w-4 text-primary" />
          <h2 className="text-base font-semibold">{t("settings.localApiAccess")}</h2>
        </div>
        <p className="text-sm text-muted-foreground">{t("settings.localApiAccessDesc")}</p>
      </div>
      <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_auto]">
        <label className="grid gap-2">
          <span className={labelClass}>{t("settings.serverApiKey")}</span>
          <input
            type="password"
            value={localApiKey}
            onChange={(event) => setLocalApiKeyState(event.target.value)}
            className={fieldClass}
            placeholder={t("settings.storedInBrowser")}
            autoComplete="current-password"
          />
        </label>
        <button
          type="submit"
          className="inline-flex items-center justify-center gap-2 self-end rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition hover:opacity-90"
        >
          <Save className="h-4 w-4" />
          {t("settings.save")}
        </button>
      </div>
      <p className="mt-2 text-xs text-muted-foreground">{t("settings.storedInBrowser")}</p>
    </form>
  );
}