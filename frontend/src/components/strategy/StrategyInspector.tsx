import { useCallback, useEffect, useRef, useState } from "react";
import type { KeyboardEvent as ReactKeyboardEvent } from "react";
import { useTranslation } from "react-i18next";
import { createPortal } from "react-dom";
import { useNavigate } from "react-router";
import { X } from "lucide-react";
import { api, ApiError, type StrategyDetail } from "@/lib/api";
import { cn } from "@/lib/utils";
import { kindKey, readinessKey } from "./labels";
import { OverviewPane, ContractPane } from "./StrategyInspectorPanes";
import {
  EvidencePane,
  ReviewPane,
  RunsPane,
  SchedulePane,
} from "./StrategyInspectorReviewPanes";
import type { InspectorTab } from "./StrategyInspectorShared";

interface StrategyInspectorProps {
  strategyId: string;
  onClose: () => void;
}

type TabLabelKey =
  | "strategyLibrary.inspectorTabOverview"
  | "strategyLibrary.inspectorTabContract"
  | "strategyLibrary.inspectorTabReview"
  | "strategyLibrary.inspectorTabEvidence"
  | "strategyLibrary.inspectorTabSchedule"
  | "strategyLibrary.inspectorTabRuns";

type ScheduleReasonKey =
  | "strategyLibrary.scheduleDisabledNotReady"
  | "strategyLibrary.scheduleDisabledDraft"
  | "strategyLibrary.scheduleDisabledNeedsWork"
  | "strategyLibrary.scheduleDisabledReviewing"
  | "strategyLibrary.scheduleDisabledRejected";

const TABS: Array<{ id: InspectorTab; key: TabLabelKey }> = [
  { id: "overview", key: "strategyLibrary.inspectorTabOverview" },
  { id: "contract", key: "strategyLibrary.inspectorTabContract" },
  { id: "review", key: "strategyLibrary.inspectorTabReview" },
  { id: "evidence", key: "strategyLibrary.inspectorTabEvidence" },
  { id: "schedule", key: "strategyLibrary.inspectorTabSchedule" },
  { id: "runs", key: "strategyLibrary.inspectorTabRuns" },
];

const FOCUSABLE =
  'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

const READINESS_TONE: Record<string, string> = {
  draft: "bg-muted text-muted-foreground",
  needs_work: "bg-warning/10 text-warning",
  reviewing: "bg-muted text-muted-foreground",
  ready: "bg-success/10 text-success",
  rejected: "bg-danger/10 text-danger",
};

function disabledReason(detail: StrategyDetail | null): ScheduleReasonKey | null {
  if (!detail) return "strategyLibrary.scheduleDisabledNotReady";
  switch (detail.readiness) {
    case "draft":
      return "strategyLibrary.scheduleDisabledDraft";
    case "needs_work":
      return detail.gaps.length > 0
        ? "strategyLibrary.scheduleDisabledNeedsWork"
        : "strategyLibrary.scheduleDisabledNotReady";
    case "reviewing":
      return "strategyLibrary.scheduleDisabledReviewing";
    case "rejected":
      return "strategyLibrary.scheduleDisabledRejected";
    case "ready":
      return null;
  }
}

/**
 * Read-only strategy inspector. It never writes: the only action navigates to
 * the schedule page once the backend has marked the strategy ready.
 */
export function StrategyInspector({ strategyId, onClose }: StrategyInspectorProps) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [detail, setDetail] = useState<StrategyDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [reloadToken, setReloadToken] = useState(0);
  const [tab, setTab] = useState<InspectorTab>("overview");
  const [entered, setEntered] = useState(false);

  const drawerRef = useRef<HTMLElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const restoreRef = useRef<HTMLElement | null>(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    void api
      .getStrategy(strategyId)
      .then((loaded) => {
        if (!cancelled) setDetail(loaded);
      })
      .catch((loadError: unknown) => {
        if (cancelled) return;
        setDetail(null);
        setError(
          loadError instanceof ApiError
            ? loadError.message
            : t("strategyLibrary.inspectorError"),
        );
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [strategyId, reloadToken, t]);

  useEffect(() => {
    restoreRef.current =
      document.activeElement instanceof HTMLElement ? document.activeElement : null;
    closeRef.current?.focus();
    const timer = window.setTimeout(() => setEntered(true), 0);
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onCloseRef.current();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      window.clearTimeout(timer);
      document.removeEventListener("keydown", onKeyDown);
      const restore = restoreRef.current;
      if (restore?.isConnected) restore.focus();
    };
  }, []);

  const trapFocus = useCallback((event: ReactKeyboardEvent<HTMLElement>) => {
    if (event.key !== "Tab") return;
    const nodes = drawerRef.current?.querySelectorAll<HTMLElement>(FOCUSABLE);
    if (!nodes || nodes.length === 0) return;
    const first = nodes[0];
    const last = nodes[nodes.length - 1];
    const active = document.activeElement;
    if (event.shiftKey && (active === first || !drawerRef.current?.contains(active))) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && active === last) {
      event.preventDefault();
      first.focus();
    }
  }, []);

  const ready = detail?.readiness === "ready";
  const reasonKey = disabledReason(detail);
  const reason = reasonKey
    ? t(reasonKey, { count: detail?.gaps.length ?? 0 })
    : undefined;
  const title = detail?.name ?? t("strategyLibrary.inspectorTitle");

  const body = !detail && loading ? (
    <p className="px-4 py-6 text-sm text-muted-foreground">
      {t("strategyLibrary.inspectorLoading")}
    </p>
  ) : error ? (
    <div
      role="alert"
      className="mx-4 my-4 flex flex-wrap items-center gap-3 rounded-md border border-danger/40 bg-danger/5 px-3 py-2 text-sm"
    >
      <span className="text-danger">{error}</span>
      <button
        type="button"
        onClick={() => setReloadToken((token) => token + 1)}
        className="rounded-md border px-3 py-1 text-xs transition hover:bg-muted"
      >
        {t("strategyLibrary.retry")}
      </button>
    </div>
  ) : detail ? (
    <>
      <div
        role="tablist"
        aria-label={t("strategyLibrary.inspectorTabsAria")}
        className="flex shrink-0 gap-1 overflow-x-auto border-b px-2"
      >
        {TABS.map((entry) => {
          const selected = entry.id === tab;
          return (
            <button
              key={entry.id}
              type="button"
              role="tab"
              id={`inspector-tab-${entry.id}`}
              aria-selected={selected}
              aria-controls={`inspector-panel-${entry.id}`}
              tabIndex={selected ? 0 : -1}
              onClick={() => setTab(entry.id)}
              className={cn(
                "shrink-0 border-b-2 px-2.5 py-2 text-xs transition-colors",
                selected
                  ? "border-primary font-medium text-foreground"
                  : "border-transparent text-muted-foreground hover:text-foreground",
              )}
            >
              {t(entry.key)}
            </button>
          );
        })}
      </div>
      <div
        role="tabpanel"
        id={`inspector-panel-${tab}`}
        aria-labelledby={`inspector-tab-${tab}`}
        className="flex-1 overflow-y-auto px-4 py-4"
      >
        {tab === "overview" && <OverviewPane detail={detail} />}
        {tab === "contract" && <ContractPane detail={detail} />}
        {tab === "review" && <ReviewPane detail={detail} />}
        {tab === "evidence" && <EvidencePane detail={detail} />}
        {tab === "schedule" && <SchedulePane detail={detail} />}
        {tab === "runs" && <RunsPane detail={detail} />}
      </div>
    </>
  ) : null;

  return createPortal(
    <div className="fixed inset-0 z-50 flex justify-end bg-black/40" onClick={onClose}>
      <aside
        ref={drawerRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="strategy-inspector-title"
        onKeyDown={trapFocus}
        onClick={(event) => event.stopPropagation()}
        className={cn(
          "flex h-full w-[480px] max-w-full flex-col border-s bg-background shadow-xl transition-transform duration-200 ease-out motion-reduce:transition-none",
          entered ? "translate-x-0" : "translate-x-full rtl:-translate-x-full",
        )}
      >
        <header className="flex shrink-0 items-start justify-between gap-3 border-b px-4 py-3">
          <div className="min-w-0">
            <h2 id="strategy-inspector-title" className="truncate text-sm font-semibold">
              {title}
            </h2>
            {detail && (
              <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                <span>{t(kindKey(detail.kind))}</span>
                <span
                  className={cn(
                    "rounded px-1.5 py-0.5 font-medium",
                    READINESS_TONE[detail.readiness],
                  )}
                >
                  {t(readinessKey(detail.readiness))}
                </span>
                <span className="font-mono">
                  {t("strategyLibrary.inspectorVersion", { version: detail.version })}
                </span>
                {detail.demo && (
                  <span className="rounded bg-primary/10 px-1 py-0.5 text-[10px] font-medium text-primary">
                    {t("strategyLibrary.demoBadge")}
                  </span>
                )}
              </div>
            )}
          </div>
          <div className="flex shrink-0 items-center gap-1.5">
            <button
              type="button"
              disabled={!ready}
              title={reason}
              onClick={() => {
                if (ready) navigate("/strategy-schedules");
              }}
              className="rounded-md border px-2.5 py-1 text-xs font-medium transition hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50"
            >
              {t("strategyLibrary.actionEnableSchedule")}
            </button>
            <button
              ref={closeRef}
              type="button"
              onClick={onClose}
              aria-label={t("strategyLibrary.inspectorCloseAria")}
              className="inline-flex items-center gap-1 rounded-md border px-2 py-1 text-xs text-muted-foreground transition hover:bg-muted"
            >
              <X className="h-3.5 w-3.5" aria-hidden />
              {t("strategyLibrary.inspectorClose")}
            </button>
          </div>
        </header>
        {body}
      </aside>
    </div>,
    document.body,
  );
}