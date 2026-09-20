import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router";
import { LogOut, UserRound } from "lucide-react";
import { api } from "@/lib/api";
import { useAuthStore } from "@/stores/auth";

/**
 * Signed-in identity and sign-out. Renders nothing when no session exists, so
 * flag-off deployments (D19) see the footer exactly as before.
 */
export function UserMenu({ collapsed = false }: { collapsed?: boolean }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const username = useAuthStore((state) => state.username);
  const displayName = useAuthStore((state) => state.displayName);
  const role = useAuthStore((state) => state.role);
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const closeOnOutsideClick = (event: MouseEvent | TouchEvent) => {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", closeOnOutsideClick);
    document.addEventListener("touchstart", closeOnOutsideClick, { passive: true });
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("mousedown", closeOnOutsideClick);
      document.removeEventListener("touchstart", closeOnOutsideClick);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [open]);

  if (!username) return null;

  const signOut = async () => {
    try {
      await api.auth.logout();
    } catch { /* the local session is torn down regardless */ }
    useAuthStore.getState().clearSession();
    navigate("/login", { replace: true });
  };

  if (collapsed) {
    return (
      <button
        type="button"
        onClick={() => void signOut()}
        title={t("auth.logout")}
        className="p-1.5 text-muted-foreground hover:text-foreground rounded transition-colors"
      >
        <LogOut className="h-3.5 w-3.5" aria-hidden="true" />
      </button>
    );
  }

  return (
    <div ref={containerRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        aria-label={t("auth.account")}
        className="flex w-full items-center gap-2 rounded-md p-1.5 text-xs text-muted-foreground transition-colors hover:bg-muted/60 hover:text-foreground"
      >
        <UserRound className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
        <span className="min-w-0 flex-1 truncate text-start">{displayName || username}</span>
        {role === "admin" && (
          <span className="shrink-0 rounded bg-primary/10 px-1 text-[10px] text-primary">
            {t("auth.adminBadge")}
          </span>
        )}
      </button>
      {open && (
        <div className="absolute bottom-full start-0 z-50 mb-1 w-full rounded-md border border-border/60 bg-popover p-1 shadow-lg">
          <p className="px-2 py-1.5 text-[10px] text-muted-foreground/70">
            {t("auth.signedInAs", { username })}
          </p>
          <button
            type="button"
            onClick={() => void signOut()}
            className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          >
            <LogOut className="h-3 w-3" aria-hidden="true" />
            {t("auth.logout")}
          </button>
        </div>
      )}
    </div>
  );
}