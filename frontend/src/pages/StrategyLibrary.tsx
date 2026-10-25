import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate, useSearchParams } from "react-router";
import { RefreshCw, Search } from "lucide-react";
import {
  api,
  ApiError,
  type StrategyKind,
  type StrategyLibrarySummary,
  type StrategyReadiness,
  type StrategySummary,
} from "@/lib/api";
import { Skeleton } from "@/components/common/Skeleton";
import { StrategyTable } from "@/components/strategy/StrategyTable";
import { StrategyInspector } from "@/components/strategy/StrategyInspector";
import { DemoStoryboard } from "@/components/strategy/DemoStoryboard";
import { kindKey, readinessKey } from "@/components/strategy/labels";

const SEARCH_DEBOUNCE_MS = 150;
const FILTER_CLASS =
  "rounded-md border bg-background px-2 py-1.5 text-xs outline-none transition focus:border-primary";
const KIND_OPTIONS: Array<StrategyKind | "all"> = ["all", "code", "workflow"];
const READINESS_OPTIONS: Array<StrategyReadiness | "all"> = [
  "all",
  "draft",
  "needs_work",
  "reviewing",
  "ready",
  "rejected",
];

function TableSkeleton({ label }: { label: string }) {
  return (
    <div className="divide-y rounded-md border bg-card" role="status" aria-label={label}>
      {Array.from({ length: 8 }).map((_, index) => (
        <div key={index} className="flex h-9 items-center gap-6 px-3" aria-hidden>
          <Skeleton className="h-3 w-40" />
          <Skeleton className="h-3 w-12" />
          <Skeleton className="h-3 w-16" />
          <Skeleton className="h-3 w-20" />
          <Skeleton className="h-3 w-10" />
          <Skeleton className="h-3 w-14" />
          <Skeleton className="h-3 w-20" />
        </div>
      ))}
    </div>
  );
}

function formatSuccessRate(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return "—";
  return `${Math.round(value * 100)}%`;
}

export function StrategyLibrary() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();

  const [items, setItems] = useState<StrategySummary[]>([]);
  const [total, setTotal] = useState(0);
  const [summary, setSummary] = useState<StrategyLibrarySummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [kind, setKind] = useState<StrategyKind | "all">("all");
  const [readiness, setReadiness] = useState<StrategyReadiness | "all">("all");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [seeding, setSeeding] = useState(false);
  const [seedError, setSeedError] = useState<string | null>(null);
  const [demoOpenRequest, setDemoOpenRequest] = useState(0);

  const load = useCallback(
    async (mode: "initial" | "refresh") => {
      if (mode === "initial") setLoading(true);
      setError(null);
      try {
        const [list, stats] = await Promise.all([
          api.listStrategies(),
          // The summary is a convenience: a failure there must not hide the
          // list, so it degrades to em dashes instead of rejecting the load.
          api.getStrategySummary().catch(() => null),
        ]);
        setItems(Array.isArray(list.items) ? list.items : []);
        setTotal(typeof list.total === "number" ? list.total : list.items.length);
        setSummary(stats);
      } catch (loadError) {
        setItems([]);
        setError(
          loadError instanceof ApiError
            ? loadError.message
            : t("strategyLibrary.loadError"),
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

  useEffect(() => {
    const timer = setTimeout(
      () => setDebouncedQuery(query.trim().toLowerCase()),
      SEARCH_DEBOUNCE_MS,
    );
    return () => clearTimeout(timer);
  }, [query]);

  const filtered = useMemo(
    () =>
      items.filter((item) => {
        if (kind !== "all" && item.kind !== kind) return false;
        if (readiness !== "all" && item.readiness !== readiness) return false;
        if (debouncedQuery && !item.name.toLowerCase().includes(debouncedQuery)) return false;
        return true;
      }),
    [items, kind, readiness, debouncedQuery],
  );

  const readyCount = useMemo(
    () => items.filter((item) => item.readiness === "ready").length,
    [items],
  );
  const needsWorkCount = useMemo(
    () => items.filter((item) => item.readiness === "needs_work").length,
    [items],
  );

  // `?demo=1` only opens the inspector once, and only when a demo row exists.
  const demoRequested = searchParams.get("demo") === "1";
  const demoOpened = useRef(false);
  useEffect(() => {
    if (!demoRequested || demoOpened.current || items.length === 0) return;
    demoOpened.current = true;
    const demo = items.find((item) => item.demo);
    if (demo) setSelectedId(demo.id);
  }, [demoRequested, items]);

  async function handleSeedDemo() {
    setSeeding(true);
    setSeedError(null);
    try {
      const seeded = await api.seedDemoStrategy();
      await load("refresh");
      setSelectedId(seeded.strategy_id);
    } catch (seedLoadError) {
      setSeedError(
        seedLoadError instanceof ApiError
          ? seedLoadError.message
          : t("strategyLibrary.demoLoadError"),
      );
    } finally {
      setSeeding(false);
    }
  }

  const kpis = [
    { label: t("strategyLibrary.kpiTotal"), value: String(total) },
    { label: t("strategyLibrary.kpiReady"), value: String(readyCount) },
    { label: t("strategyLibrary.kpiNeedsWork"), value: String(needsWorkCount) },
    {
      label: t("strategyLibrary.kpiActiveSchedules"),
      value: summary ? String(summary.active_count) : "—",
    },
    {
      label: t("strategyLibrary.kpiSuccessRate"),
      value: formatSuccessRate(summary?.success_rate_7d ?? null),
    },
  ];

  const hasRows = items.length > 0;
  const showInitialError = error !== null && !hasRows;

  return (
    <div className="mx-auto flex w-full max-w-7xl flex-col gap-3 p-4 lg:p-6">
      <header className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-sm font-semibold">{t("strategyLibrary.title")}</h1>
        <div className="flex items-center gap-1.5">
          <button
            type="button"
            onClick={() => setDemoOpenRequest((request) => request + 1)}
            className="rounded-md border px-2.5 py-1 text-xs text-muted-foreground transition hover:bg-muted"
          >
            {t("strategyLibrary.demoOpen")}
          </button>
          <button
            type="button"
            onClick={() => void load("refresh")}
            className="inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-xs text-muted-foreground transition hover:bg-muted"
          >
            <RefreshCw className="h-3.5 w-3.5" aria-hidden />
            {t("strategyLibrary.refresh")}
          </button>
        </div>
      </header>

      <dl className="grid grid-cols-2 gap-px overflow-hidden rounded-md border bg-border sm:grid-cols-5">
        {kpis.map((kpi) => (
          <div key={kpi.label} className="bg-card px-3 py-2">
            <dt className="text-xs text-muted-foreground">{kpi.label}</dt>
            <dd className="text-base font-semibold tabular-nums">{kpi.value}</dd>
          </div>
        ))}
      </dl>

      <div className="flex flex-wrap items-center gap-2">
        <label className="relative block min-w-48 flex-1 sm:max-w-64">
          <Search className="pointer-events-none absolute start-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={t("strategyLibrary.searchPlaceholder")}
            aria-label={t("strategyLibrary.searchPlaceholder")}
            className="w-full rounded-md border bg-background py-1.5 ps-8 pe-2 text-xs outline-none transition focus:border-primary"
          />
        </label>

        <label className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
          <span>{t("strategyLibrary.filterKind")}</span>
          <select
            value={kind}
            aria-label={t("strategyLibrary.filterKind")}
            onChange={(event) => setKind(event.target.value as StrategyKind | "all")}
            className={FILTER_CLASS}
          >
            {KIND_OPTIONS.map((option) => (
              <option key={option} value={option}>
                {option === "all"
                  ? t("strategyLibrary.kindAll")
                  : t(kindKey(option))}
              </option>
            ))}
          </select>
        </label>

        <label className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
          <span>{t("strategyLibrary.filterReadiness")}</span>
          <select
            value={readiness}
            aria-label={t("strategyLibrary.filterReadiness")}
            onChange={(event) =>
              setReadiness(event.target.value as StrategyReadiness | "all")
            }
            className={FILTER_CLASS}
          >
            {READINESS_OPTIONS.map((option) => (
              <option key={option} value={option}>
                {option === "all"
                  ? t("strategyLibrary.readinessAll")
                  : t(readinessKey(option))}
              </option>
            ))}
          </select>
        </label>
      </div>

      {error && hasRows && (
        <p role="alert" className="rounded-md border border-danger/40 bg-danger/5 px-3 py-2 text-xs text-danger">
          {error}
        </p>
      )}

      {loading ? (
        <TableSkeleton label={t("strategyLibrary.loading")} />
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
        <section className="flex flex-col items-center gap-3 rounded-md border bg-card px-6 py-10 text-center">
          <h2 className="text-sm font-medium">{t("strategyLibrary.emptyTitle")}</h2>
          <ol className="flex flex-col gap-1 text-xs text-muted-foreground sm:flex-row sm:gap-4">
            <li>1. {t("strategyLibrary.emptyStep1")}</li>
            <li>2. {t("strategyLibrary.emptyStep2")}</li>
            <li>3. {t("strategyLibrary.emptyStep3")}</li>
          </ol>
          <button
            type="button"
            onClick={() => void handleSeedDemo()}
            disabled={seeding}
            className="rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {seeding
              ? t("strategyLibrary.emptyLoadingDemo")
              : t("strategyLibrary.emptyLoadDemo")}
          </button>
          {seedError && (
            <p role="alert" className="text-xs text-danger">
              {seedError}
            </p>
          )}
        </section>
      ) : filtered.length === 0 ? (
        <p className="rounded-md border bg-card px-4 py-8 text-center text-sm text-muted-foreground">
          {t("strategyLibrary.noMatches")}
        </p>
      ) : (
        <StrategyTable
          items={filtered}
          onOpen={(strategyId) => setSelectedId(strategyId)}
          onSchedule={() => navigate("/strategy-schedules")}
        />
      )}

      {selectedId && (
        <StrategyInspector
          strategyId={selectedId}
          onClose={() => setSelectedId(null)}
        />
      )}

      <DemoStoryboard openRequest={demoOpenRequest} />
    </div>
  );
}