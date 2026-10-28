import { Fragment, useState } from "react";
import { useTranslation } from "react-i18next";
import { ChevronDown, ChevronUp, ListChecks, Loader2, Play } from "lucide-react";
import type { TFunction } from "i18next";
import type { StrategySchedule, StrategySummary } from "@/lib/api";
import {
  describeCadence,
  formatIntervalMs,
  formatWallTime,
  formatWeekdays,
} from "@/lib/cadence";
import { cn } from "@/lib/utils";
import { Relative, RunBadge, RunDetails, RunsDrawer } from "./ScheduleRuns";

interface ScheduleTableProps {
  schedules: StrategySchedule[];
  strategiesById: Record<string, StrategySummary>;
  busyJobId: string | null;
  onRunNow: (jobId: string) => void;
  onDetach: (jobId: string) => void;
}

const PAUSED_STATUSES = new Set(["paused", "cancelled", "expired", "failed"]);

export function isPausedStatus(status: string): boolean {
  return PAUSED_STATUSES.has(status);
}

export function cadenceLabel(schedule: string, t: TFunction, locale: string): string {
  const cadence = describeCadence(schedule);
  switch (cadence.kind) {
    case "interval":
      return t("strategySchedules.cadenceInterval", {
        interval: formatIntervalMs(cadence.ms),
      });
    case "daily":
      return t("strategySchedules.cadenceDaily", {
        time: formatWallTime(cadence.hour, cadence.minute),
      });
    case "weekly":
      return t("strategySchedules.cadenceWeekly", {
        days: formatWeekdays(cadence.weekdays, locale),
        time: formatWallTime(cadence.hour, cadence.minute),
      });
    case "cron":
      return t("strategySchedules.cadenceCron", { expression: cadence.expression });
  }
}

export function ScheduleTable({
  schedules,
  strategiesById,
  busyJobId,
  onRunNow,
  onDetach,
}: ScheduleTableProps) {
  const { t, i18n } = useTranslation();
  const [expandedJobId, setExpandedJobId] = useState<string | null>(null);
  const [drawerSchedule, setDrawerSchedule] = useState<StrategySchedule | null>(null);
  const [pendingDetach, setPendingDetach] = useState<string | null>(null);

  function nameFor(schedule: StrategySchedule): string {
    return strategiesById[schedule.strategy_id]?.name ?? schedule.strategy_id;
  }

  return (
    <>
      <div className="overflow-x-auto rounded-md border bg-card">
        <table className="w-full text-[13px]">
          <thead>
            <tr className="border-b bg-muted/40 text-left text-xs text-muted-foreground">
              <th className="px-3 py-2 font-medium">
                {t("strategySchedules.columnsStrategy")}
              </th>
              <th className="px-3 py-2 font-medium">
                {t("strategySchedules.columnsCadence")}
              </th>
              <th className="px-3 py-2 font-medium">{t("strategySchedules.columnsNext")}</th>
              <th className="px-3 py-2 font-medium">
                {t("strategySchedules.columnsStatus")}
              </th>
              <th className="px-3 py-2 font-medium">
                {t("strategySchedules.columnsLastFive")}
              </th>
              <th className="px-3 py-2 text-right font-medium">
                {t("strategySchedules.columnsActions")}
              </th>
            </tr>
          </thead>
          <tbody>
            {schedules.map((schedule) => {
              const name = nameFor(schedule);
              const expanded = expandedJobId === schedule.job_id;
              const paused = isPausedStatus(schedule.status);
              const recent = schedule.last_runs.slice(0, 5);
              return (
                <Fragment key={schedule.job_id}>
                  <tr
                    data-testid={`schedule-row-${schedule.job_id}`}
                    className="border-b border-border/40 last:border-0"
                  >
                    <td className="px-3 py-2 align-middle">
                      <div className="flex min-w-0 flex-col">
                        <span className="truncate font-medium">{name}</span>
                        <span className="font-mono text-[11px] text-muted-foreground">
                          {t("strategyLibrary.inspectorVersion", {
                            version: schedule.strategy_version,
                          })}
                        </span>
                      </div>
                    </td>
                    <td className="px-3 py-2 align-middle text-muted-foreground">
                      {cadenceLabel(schedule.schedule, t, i18n.language)}
                    </td>
                    <td className="px-3 py-2 align-middle">
                      {Number.isFinite(schedule.next_run_at) && schedule.next_run_at > 0 ? (
                        <Relative epochMs={schedule.next_run_at} />
                      ) : (
                        <span className="text-xs text-muted-foreground">
                          {t("strategySchedules.runnerNoNext")}
                        </span>
                      )}
                    </td>
                    <td className="px-3 py-2 align-middle">
                      <span
                        className={cn(
                          "inline-flex rounded px-1.5 py-0.5 text-xs font-medium",
                          paused
                            ? "bg-muted text-muted-foreground"
                            : "bg-success/10 text-success",
                        )}
                      >
                        {t(
                          paused
                            ? "strategySchedules.statusPaused"
                            : "strategySchedules.statusActive",
                        )}
                      </span>
                    </td>
                    <td className="px-3 py-2 align-middle">
                      {recent.length === 0 ? (
                        <span className="text-xs text-muted-foreground">
                          {t("strategySchedules.noRuns")}
                        </span>
                      ) : (
                        <div className="flex flex-wrap items-center gap-1">
                          {recent.map((run) => (
                            <RunBadge key={run.run_id} status={run.status} />
                          ))}
                        </div>
                      )}
                    </td>
                    <td className="px-3 py-2 align-middle">
                      <div className="flex flex-wrap items-center justify-end gap-1">
                        <button
                          type="button"
                          aria-expanded={expanded}
                          aria-label={t(
                            expanded
                              ? "strategySchedules.ariaCollapse"
                              : "strategySchedules.ariaExpand",
                            { name },
                          )}
                          onClick={() =>
                            setExpandedJobId(expanded ? null : schedule.job_id)
                          }
                          className="inline-flex items-center gap-1 rounded border px-2 py-0.5 text-xs text-muted-foreground transition hover:bg-muted"
                        >
                          {expanded ? (
                            <ChevronUp className="h-3.5 w-3.5" aria-hidden />
                          ) : (
                            <ChevronDown className="h-3.5 w-3.5" aria-hidden />
                          )}
                          {t(
                            expanded
                              ? "strategySchedules.collapse"
                              : "strategySchedules.expand",
                          )}
                        </button>
                        <button
                          type="button"
                          aria-label={t("strategySchedules.ariaViewRecent", { name })}
                          onClick={() => setDrawerSchedule(schedule)}
                          className="inline-flex items-center gap-1 rounded border px-2 py-0.5 text-xs text-muted-foreground transition hover:bg-muted"
                        >
                          <ListChecks className="h-3.5 w-3.5" aria-hidden />
                          {t("strategySchedules.viewRecent")}
                        </button>
                        <button
                          type="button"
                          aria-label={t("strategySchedules.ariaRunNow", { name })}
                          onClick={() => onRunNow(schedule.job_id)}
                          disabled={busyJobId === schedule.job_id}
                          className="inline-flex items-center gap-1 rounded border px-2 py-0.5 text-xs text-muted-foreground transition hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50"
                        >
                          {busyJobId === schedule.job_id ? (
                            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
                          ) : (
                            <Play className="h-3.5 w-3.5" aria-hidden />
                          )}
                          {t("strategySchedules.runNow")}
                        </button>
                        {pendingDetach === schedule.job_id ? (
                          <>
                            <button
                              type="button"
                              onClick={() => setPendingDetach(null)}
                              className="rounded border px-2 py-0.5 text-xs text-muted-foreground transition hover:bg-muted"
                            >
                              {t("layout.cancel")}
                            </button>
                            <button
                              type="button"
                              onClick={() => {
                                setPendingDetach(null);
                                onDetach(schedule.job_id);
                              }}
                              className="rounded border border-danger bg-danger/10 px-2 py-0.5 text-xs text-danger transition"
                            >
                              {t("strategySchedules.confirmDetach")}
                            </button>
                          </>
                        ) : (
                          <button
                            type="button"
                            aria-label={t("strategySchedules.ariaDetach", { name })}
                            onClick={() => setPendingDetach(schedule.job_id)}
                            className="rounded border px-2 py-0.5 text-xs text-muted-foreground transition hover:bg-muted"
                          >
                            {t("strategySchedules.detach")}
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                  {expanded && (
                    <tr className="border-b border-border/40 bg-muted/20 last:border-0">
                      <td colSpan={6} className="px-3 py-2">
                        <RunDetails runs={recent} />
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>

      {drawerSchedule && (
        <RunsDrawer
          schedule={drawerSchedule}
          strategyName={nameFor(drawerSchedule)}
          onClose={() => setDrawerSchedule(null)}
        />
      )}
    </>
  );
}