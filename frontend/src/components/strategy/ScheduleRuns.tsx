import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import { Loader2, X } from "lucide-react";
import {
  api,
  ApiError,
  type StrategyRun,
  type StrategySchedule,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import { relativeTimeFromEpoch, runStatusKey } from "./labels";

// Text plus a distinct glyph so the four run states never rely on colour.
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

export function RunBadge({ status }: { status: string }) {
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

export function Relative({ epochMs }: { epochMs: number }) {
  const { t } = useTranslation();
  const value = relativeTimeFromEpoch(epochMs);
  return <>{t(value.key, { count: value.count })}</>;
}

function durationSeconds(run: StrategyRun): number | null {
  if (run.finished_at === null) return null;
  return Math.max(0, Math.round((run.finished_at - run.started_at) / 1000));
}

export function RunDetails({ runs }: { runs: StrategyRun[] }) {
  const { t } = useTranslation();
  if (runs.length === 0) {
    return (
      <p className="px-3 py-2 text-xs text-muted-foreground">
        {t("strategySchedules.noRuns")}
      </p>
    );
  }
  return (
    <ul className="divide-y rounded-md border bg-background/60">
      {runs.map((run) => {
        const seconds = durationSeconds(run);
        return (
          <li
            key={run.run_id}
            data-testid={`schedule-run-detail-${run.run_id}`}
            className="flex flex-wrap items-center gap-x-4 gap-y-1 px-3 py-1.5 text-xs"
          >
            <RunBadge status={run.status} />
            <span className="text-muted-foreground">
              <Relative epochMs={run.started_at} />
            </span>
            <span className="tabular-nums text-muted-foreground">
              {seconds === null
                ? "—"
                : t("strategySchedules.duration", { seconds })}
            </span>
            <span className="font-mono text-[11px] text-muted-foreground">
              {t("strategySchedules.runLabel", { id: run.run_id })}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

interface RunsDrawerProps {
  schedule: StrategySchedule;
  strategyName: string;
  onClose: () => void;
}

export function RunsDrawer({ schedule, strategyName, onClose }: RunsDrawerProps) {
  const { t } = useTranslation();
  const [runs, setRuns] = useState<StrategyRun[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const seq = useRef(0);

  useEffect(() => {
    const id = ++seq.current;
    setRuns(null);
    setError(null);
    api
      .getStrategyScheduleRuns(schedule.job_id, 20)
      .then((rows) => {
        if (id === seq.current) setRuns(rows.slice(0, 20));
      })
      .catch((loadError: unknown) => {
        if (id !== seq.current) return;
        setError(
          loadError instanceof ApiError
            ? loadError.message
            : t("strategySchedules.drawerError"),
        );
      });
    return () => {
      seq.current += 1;
    };
  }, [schedule.job_id, t]);

  return createPortal(
    <div className="fixed inset-0 z-50 flex justify-end bg-black/40" onClick={onClose}>
      <aside
        role="dialog"
        aria-modal="true"
        aria-label={t("strategySchedules.drawerTitle")}
        data-testid="schedule-runs-drawer"
        onClick={(event) => event.stopPropagation()}
        className="flex h-full w-[460px] max-w-full flex-col border-s bg-background shadow-xl"
      >
        <header className="flex shrink-0 items-start justify-between gap-3 border-b px-4 py-3">
          <div className="min-w-0">
            <h2 className="text-sm font-semibold">
              {t("strategySchedules.drawerTitle")}
            </h2>
            <p className="truncate text-xs text-muted-foreground">{strategyName}</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label={t("strategySchedules.drawerClose")}
            className="inline-flex items-center gap-1 rounded-md border px-2 py-1 text-xs text-muted-foreground transition hover:bg-muted"
          >
            <X className="h-3.5 w-3.5" aria-hidden />
            {t("strategySchedules.drawerClose")}
          </button>
        </header>
        <div className="flex-1 overflow-y-auto p-4">
          {error ? (
            <p role="alert" className="text-sm text-danger">
              {error}
            </p>
          ) : runs === null ? (
            <div className="flex items-center justify-center gap-2 p-6 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
              {t("strategySchedules.drawerLoading")}
            </div>
          ) : runs.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              {t("strategySchedules.noRuns")}
            </p>
          ) : (
            <RunDetails runs={runs} />
          )}
        </div>
      </aside>
    </div>,
    document.body,
  );
}