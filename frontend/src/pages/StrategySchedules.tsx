import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { Loader2, Plus, RefreshCw } from "lucide-react";
import { toast } from "sonner";
import {
  api,
  ApiError,
  type StrategySchedule,
  type StrategySummary,
} from "@/lib/api";
import { relativeTimeFromEpoch } from "@/components/strategy/labels";
import { isPausedStatus, ScheduleTable } from "@/components/strategy/ScheduleTable";
import { CreateScheduleModal } from "@/components/strategy/CreateScheduleModal";

function relativeLabel(t: TFunction, epochMs: number) {
  const value = relativeTimeFromEpoch(epochMs);
  return t(value.key, { count: value.count });
}

export function StrategySchedules() {
  const { t } = useTranslation();
  const [schedules, setSchedules] = useState<StrategySchedule[]>([]);
  const [strategies, setStrategies] = useState<StrategySummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyJobId, setBusyJobId] = useState<string | null>(null);
  const [modalOpen, setModalOpen] = useState(false);

  const load = useCallback(
    async (mode: "initial" | "refresh") => {
      if (mode === "initial") setLoading(true);
      setError(null);
      try {
        const [rows, library] = await Promise.all([
          api.listStrategySchedules(),
          // Names are a convenience; a failed library read must not hide the
          // schedule list, so it degrades to the raw strategy id.
          api.listStrategies({ limit: 200 }).catch(() => null),
        ]);
        setSchedules(Array.isArray(rows) ? rows : []);
        setStrategies(library?.items ?? []);
      } catch (loadError) {
        if (mode === "initial") setSchedules([]);
        setError(
          loadError instanceof ApiError
            ? loadError.message
            : t("strategySchedules.loadError"),
        );
      } finally {
        setLoading(false);
      }
    },
    [t],
  );

  useEffect(() => {
    void load("initial");
  }, [load]);

  const strategiesById = useMemo(
    () => Object.fromEntries(strategies.map((strategy) => [strategy.id, strategy])),
    [strategies],
  );

  const activeCount = useMemo(
    () => schedules.filter((schedule) => !isPausedStatus(schedule.status)).length,
    [schedules],
  );
  const pausedCount = schedules.length - activeCount;
  const nextRunAt = useMemo(() => {
    let earliest: number | null = null;
    for (const schedule of schedules) {
      if (!Number.isFinite(schedule.next_run_at) || schedule.next_run_at <= 0) continue;
      earliest = earliest === null ? schedule.next_run_at : Math.min(earliest, schedule.next_run_at);
    }
    return earliest;
  }, [schedules]);

  async function handleRunNow(jobId: string) {
    setBusyJobId(jobId);
    try {
      await api.runStrategyScheduleNow(jobId);
      await load("refresh");
      toast.success(t("strategySchedules.runNowStarted"));
    } catch (runError) {
      toast.error(
        runError instanceof ApiError
          ? runError.message
          : t("strategySchedules.actionFailed"),
      );
    } finally {
      setBusyJobId(null);
    }
  }

  async function handleDetach(jobId: string) {
    setBusyJobId(jobId);
    try {
      await api.deleteStrategySchedule(jobId);
      await load("refresh");
    } catch (detachError) {
      toast.error(
        detachError instanceof ApiError
          ? detachError.message
          : t("strategySchedules.actionFailed"),
      );
    } finally {
      setBusyJobId(null);
    }
  }

  function handleCreated(jobId?: string) {
    setModalOpen(false);
    if (jobId) toast.success(t("strategySchedules.createdJob", { jobId }));
    void load("refresh");
  }

  const hasRows = schedules.length > 0;
  const showInitialError = error !== null && !hasRows;

  return (
    <div className="mx-auto flex w-full max-w-7xl flex-col gap-3 p-4 lg:p-6">
      <header className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-sm font-semibold">{t("layout.strategySchedules")}</h1>
        <div className="flex items-center gap-1.5">
          <button
            type="button"
            onClick={() => void load("refresh")}
            className="inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-xs text-muted-foreground transition hover:bg-muted"
          >
            <RefreshCw className="h-3.5 w-3.5" aria-hidden />
            {t("strategyLibrary.refresh")}
          </button>
          <button
            type="button"
            onClick={() => setModalOpen(true)}
            className="inline-flex items-center gap-1.5 rounded-md bg-primary px-2.5 py-1 text-xs font-medium text-primary-foreground transition hover:opacity-90"
          >
            <Plus className="h-3.5 w-3.5" aria-hidden />
            {t("strategySchedules.newSchedule")}
          </button>
        </div>
      </header>

      <dl
        aria-label={t("strategySchedules.runnerTitle")}
        className="grid grid-cols-3 gap-px overflow-hidden rounded-md border bg-border"
      >
        <div className="bg-card px-3 py-2">
          <dt className="text-xs text-muted-foreground">
            {t("strategySchedules.runnerTitle")}
          </dt>
          <dd className="text-base font-semibold tabular-nums">
            {t("strategySchedules.runnerActive", { count: activeCount })}
          </dd>
        </div>
        <div className="bg-card px-3 py-2">
          <dt className="text-xs text-muted-foreground">
            {t("strategySchedules.statusPaused")}
          </dt>
          <dd className="text-base font-semibold tabular-nums">
            {t("strategySchedules.runnerPaused", { count: pausedCount })}
          </dd>
        </div>
        <div className="bg-card px-3 py-2">
          <dt className="text-xs text-muted-foreground">
            {t("strategySchedules.columnsNext")}
          </dt>
          <dd className="text-base font-semibold">
            {nextRunAt === null
              ? t("strategySchedules.runnerNoNext")
              : t("strategySchedules.runnerNext", {
                  when: relativeLabel(t, nextRunAt),
                })}
          </dd>
        </div>
      </dl>

      {error && hasRows && (
        <p
          role="alert"
          className="rounded-md border border-danger/40 bg-danger/5 px-3 py-2 text-xs text-danger"
        >
          {error}
        </p>
      )}

      {loading ? (
        <div
          role="status"
          aria-label={t("strategySchedules.loading")}
          className="flex items-center justify-center gap-2 rounded-md border bg-card p-8 text-sm text-muted-foreground"
        >
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
          {t("strategySchedules.loading")}
        </div>
      ) : showInitialError ? (
        <div
          role="alert"
          className="flex flex-wrap items-center gap-3 rounded-md border border-danger/40 bg-danger/5 px-3 py-3 text-sm"
        >
          <span className="text-danger">{error}</span>
          <button
            type="button"
            onClick={() => void load("initial")}
            className="rounded-md border px-3 py-1 text-xs transition hover:bg-muted"
          >
            {t("strategyLibrary.retry")}
          </button>
        </div>
      ) : !hasRows ? (
        <section className="flex flex-col items-center gap-2 rounded-md border bg-card px-6 py-10 text-center">
          <h2 className="text-sm font-medium">
            {t("strategySchedules.emptyTitle")}
          </h2>
          <p className="text-xs text-muted-foreground">
            {t("strategySchedules.emptyHint")}
          </p>
          <button
            type="button"
            onClick={() => setModalOpen(true)}
            className="mt-1 inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground transition hover:opacity-90"
          >
            <Plus className="h-3.5 w-3.5" aria-hidden />
            {t("strategySchedules.newSchedule")}
          </button>
        </section>
      ) : (
        <ScheduleTable
          schedules={schedules}
          strategiesById={strategiesById}
          busyJobId={busyJobId}
          onRunNow={(jobId) => void handleRunNow(jobId)}
          onDetach={(jobId) => void handleDetach(jobId)}
        />
      )}

      {modalOpen && (
        <CreateScheduleModal
          strategies={strategies}
          onClose={() => setModalOpen(false)}
          onCreated={handleCreated}
        />
      )}
    </div>
  );
}