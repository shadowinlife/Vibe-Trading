import { useTranslation } from "react-i18next";
import type { StrategyReadiness, StrategySummary } from "@/lib/api";
import { cn } from "@/lib/utils";
import {
  kindKey,
  qualityKey,
  readinessKey,
  relativeTime,
  relativeTimeFromEpoch,
  runStatusKey,
  type RelativeTime,
} from "./labels";

interface StrategyTableProps {
  items: StrategySummary[];
  onOpen: (strategyId: string) => void;
  onSchedule: (strategyId: string) => void;
}

const READINESS_TONE: Record<StrategyReadiness, string> = {
  draft: "bg-muted text-muted-foreground",
  needs_work: "bg-warning/10 text-warning",
  // Reviewing is in flight, not a success; it must never read as ready.
  reviewing: "bg-muted text-muted-foreground",
  ready: "bg-success/10 text-success",
  rejected: "bg-danger/10 text-danger",
};

const QUALITY_TONE: Record<string, string> = {
  high: "bg-success/10 text-success",
  medium: "bg-primary/10 text-primary",
  low: "bg-warning/10 text-warning",
  stale: "bg-muted text-muted-foreground",
  none: "bg-muted text-muted-foreground",
};

// Text plus glyph so the four run states stay distinguishable without colour.
const RUN_GLYPH: Record<string, string> = {
  completed: "●",
  failed: "▲",
  skipped: "–",
  missed: "○",
  unknown: "○",
};

const RUN_TONE: Record<string, string> = {
  completed: "bg-success/10 text-success",
  failed: "bg-danger/10 text-danger",
  skipped: "bg-muted text-muted-foreground",
  missed: "bg-warning/10 text-warning",
  unknown: "bg-muted text-muted-foreground",
};

function Relative({ value }: { value: RelativeTime | null }) {
  const { t } = useTranslation();
  if (!value) return null;
  return <>{t(value.key, { count: value.count })}</>;
}

function RunBadge({ status }: { status: string }) {
  const { t } = useTranslation();
  const known = status in RUN_GLYPH ? status : "unknown";
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium",
        RUN_TONE[known],
      )}
    >
      <span aria-hidden>{RUN_GLYPH[known]}</span>
      <span>{t(runStatusKey(status))}</span>
    </span>
  );
}

export function StrategyTable({ items, onOpen, onSchedule }: StrategyTableProps) {
  const { t } = useTranslation();

  return (
    <div className="overflow-x-auto rounded-md border bg-card">
      <table className="w-full text-[13px]">
        <thead>
          <tr className="border-b bg-muted/40 text-left text-xs text-muted-foreground">
            <th className="px-3 py-2 font-medium">{t("strategyLibrary.columnsName")}</th>
            <th className="px-3 py-2 font-medium">{t("strategyLibrary.columnsKind")}</th>
            <th className="px-3 py-2 font-medium">{t("strategyLibrary.columnsStatus")}</th>
            <th className="px-3 py-2 font-medium">{t("strategyLibrary.columnsEvidence")}</th>
            <th className="px-3 py-2 font-medium">{t("strategyLibrary.columnsVersion")}</th>
            <th className="px-3 py-2 font-medium">{t("strategyLibrary.columnsSchedules")}</th>
            <th className="px-3 py-2 font-medium">{t("strategyLibrary.columnsLastRun")}</th>
            <th className="px-3 py-2 text-right font-medium">{t("strategyLibrary.columnsActions")}</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => {
            const verified = relativeTime(item.evidence.last_verified);
            const lastRun = item.last_run;
            const nextRun = item.schedule.next_run_at
              ? relativeTimeFromEpoch(item.schedule.next_run_at)
              : null;
            const schedulable = item.readiness === "ready";
            return (
              <tr
                key={item.id}
                data-testid={`strategy-row-${item.id}`}
                tabIndex={0}
                aria-label={t("strategyLibrary.rowOpenAria", { name: item.name })}
                onClick={() => onOpen(item.id)}
                onKeyDown={(event) => {
                  if (event.key !== "Enter" && event.key !== " ") return;
                  if ((event.target as HTMLElement).closest("button, a")) return;
                  event.preventDefault();
                  onOpen(item.id);
                }}
                className="h-9 cursor-pointer border-b border-border/40 transition-colors last:border-0 hover:bg-muted/40 focus:outline-none focus-visible:bg-muted/60"
              >
                <td className="px-3 py-0 align-middle">
                  <div className="flex min-w-0 items-center gap-1.5">
                    <span className="truncate font-medium">{item.name}</span>
                    {item.demo && (
                      <span className="rounded bg-primary/10 px-1 py-0.5 text-[10px] font-medium text-primary">
                        {t("strategyLibrary.demoBadge")}
                      </span>
                    )}
                  </div>
                </td>
                <td className="px-3 py-0 align-middle text-muted-foreground">
                  {t(kindKey(item.kind))}
                </td>
                <td className="px-3 py-0 align-middle">
                  <span
                    className={cn(
                      "inline-flex rounded px-1.5 py-0.5 text-xs font-medium",
                      READINESS_TONE[item.readiness],
                    )}
                  >
                    {t(readinessKey(item.readiness))}
                  </span>
                </td>
                <td className="px-3 py-0 align-middle">
                  <div className="flex items-center gap-1.5">
                    <span
                      className={cn(
                        "inline-flex rounded px-1.5 py-0.5 text-xs font-medium",
                        QUALITY_TONE[item.evidence.quality ?? "none"],
                      )}
                    >
                      {t(qualityKey(item.evidence.quality))}
                    </span>
                    <span className="text-xs text-muted-foreground">
                      {verified ? (
                        <Relative value={verified} />
                      ) : (
                        t("strategyLibrary.evidenceUnverified")
                      )}
                    </span>
                  </div>
                </td>
                <td className="px-3 py-0 align-middle font-mono text-xs text-muted-foreground">
                  {item.version}
                </td>
                <td className="px-3 py-0 align-middle">
                  <span className="tabular-nums">{item.schedule.count}</span>
                  {nextRun && (
                    <span className="ml-1.5 text-xs text-muted-foreground">
                      {t("strategyLibrary.schedulesNext", {
                        when: t(nextRun.key, { count: nextRun.count }),
                      })}
                    </span>
                  )}
                </td>
                <td className="px-3 py-0 align-middle">
                  {lastRun ? (
                    <div className="flex items-center gap-1.5">
                      <RunBadge status={lastRun.status} />
                      {lastRun.finished_at !== null && (
                        <span className="text-xs text-muted-foreground">
                          <Relative value={relativeTimeFromEpoch(lastRun.finished_at)} />
                        </span>
                      )}
                    </div>
                  ) : (
                    <span className="text-xs text-muted-foreground">
                      {t("strategyLibrary.lastRunNone")}
                    </span>
                  )}
                </td>
                <td className="px-3 py-0 align-middle" onClick={(event) => event.stopPropagation()}>
                  <div className="flex items-center justify-end gap-1">
                    <button
                      type="button"
                      onClick={(event) => {
                        event.stopPropagation();
                        onOpen(item.id);
                      }}
                      className="rounded border px-2 py-0.5 text-xs text-muted-foreground transition hover:bg-muted"
                    >
                      {t("strategyLibrary.actionView")}
                    </button>
                    <button
                      type="button"
                      onClick={(event) => {
                        event.stopPropagation();
                        if (schedulable) onSchedule(item.id);
                      }}
                      disabled={!schedulable}
                      title={schedulable ? undefined : t("strategyLibrary.actionScheduleDisabled")}
                      className="rounded border px-2 py-0.5 text-xs text-muted-foreground transition hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      {t("strategyLibrary.actionSchedule")}
                    </button>
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export type { StrategyTableProps };