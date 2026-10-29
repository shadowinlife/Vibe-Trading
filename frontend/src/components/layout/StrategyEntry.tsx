import { useEffect, useState } from "react";
import { Link, useLocation } from "react-router";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import { api, type StrategyLibrarySummary } from "@/lib/api";
import { isNavigationActive, STRATEGY_NAV_ITEMS } from "./navigation";

function formatNextRun(epochMs: number, locale: string): string {
  const date = new Date(epochMs);
  if (Number.isNaN(date.getTime())) return "";
  try {
    return new Intl.DateTimeFormat(locale, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    }).format(date);
  } catch {
    return "";
  }
}

export function StrategyEntry({ collapsed = false, inDrawer = false }: {
  collapsed?: boolean;
  inDrawer?: boolean;
}) {
  const { t, i18n } = useTranslation();
  const { pathname } = useLocation();
  const [summary, setSummary] = useState<StrategyLibrarySummary | null>(null);

  useEffect(() => {
    // The summary is optional chrome: a failed request leaves both entries
    // usable with no numbers and no message. The shared API helper exposes no
    // per-call signal, so the controller only guards the state write.
    const cancelled = new AbortController();
    api.getStrategySummary()
      .then((payload) => {
        if (!cancelled.signal.aborted) setSummary(payload);
      })
      .catch(() => {});
    return () => cancelled.abort();
  }, []);

  const nextRun = summary?.next_run_at ? formatNextRun(summary.next_run_at, i18n.language) : "";

  return (
    <div className="border-t border-border/60 pt-2">
      {!collapsed && (
        <p className={cn("mb-1 px-3 text-[10px] font-medium text-muted-foreground/70", !inDrawer && "max-md:hidden")}>
          {t("navigation.groups.strategy")}
        </p>
      )}
      <div className="space-y-0.5">
        {STRATEGY_NAV_ITEMS.map(({ id, to, icon: Icon }) => {
          const label = t(`layout.${id}`);
          const active = isNavigationActive(to, pathname);
          return (
            <Link
              key={to}
              to={to}
              aria-label={label}
              aria-current={active ? "page" : undefined}
              title={`${label} — ${t(`navigation.descriptions.${id}`)}`}
              className={cn(
                "flex items-center rounded-md text-[13px] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40",
                collapsed ? "justify-center px-2 py-1.5" : "gap-3 px-3 py-1.5",
                !collapsed && !inDrawer && "max-md:justify-center max-md:px-2",
                active
                  ? "bg-primary/10 text-primary font-medium"
                  : "text-muted-foreground hover:bg-muted/60 hover:text-foreground",
              )}
            >
              <Icon className="h-4 w-4 shrink-0" aria-hidden="true" />
              {!collapsed && <span className={cn(!inDrawer && "max-md:hidden")}>{label}</span>}
            </Link>
          );
        })}
      </div>
      {summary && !collapsed && (
        <p className={cn("mt-1 flex flex-wrap justify-end gap-x-1 px-3 text-[10px] tabular-nums text-muted-foreground/70", !inDrawer && "max-md:hidden")}>
          <span>{t("layout.strategySummaryActive", { active: summary.active_count })}</span>
          {nextRun && (
            <>
              <span aria-hidden="true">·</span>
              <span>{t("layout.strategySummaryNext", { time: nextRun })}</span>
            </>
          )}
          <span aria-hidden="true">·</span>
          <span className={cn(summary.failed_today > 0 && "text-danger")}>
            {t("layout.strategySummaryFailed", { failed: summary.failed_today })}
          </span>
        </p>
      )}
    </div>
  );
}