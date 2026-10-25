import { useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import { Loader2, X } from "lucide-react";
import {
  api,
  ApiError,
  type AttachStrategyScheduleRequest,
  type StrategySummary,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import {
  buildSchedule,
  CadenceStep,
  isCadenceValid,
  OutputStep,
  STEP_KEYS,
  StrategyStep,
  TIMEOUT_MAX,
  TIMEOUT_MIN,
  timezone,
  type CadenceDraft,
} from "./CreateScheduleSteps";

interface CreateScheduleModalProps {
  strategies: StrategySummary[];
  onClose: () => void;
  onCreated: (jobId?: string) => void;
}

type Step = 1 | 2 | 3;

const INITIAL_CADENCE: CadenceDraft = {
  mode: "daily",
  time: "09:00",
  weekdays: [1, 2, 3, 4, 5],
  intervalMinutes: "60",
  custom: "0 9 * * 1-5",
};

export function CreateScheduleModal({
  strategies,
  onClose,
  onCreated,
}: CreateScheduleModalProps) {
  const { t } = useTranslation();
  const ready = strategies.filter((strategy) => strategy.readiness === "ready");
  const excluded = strategies.length - ready.length;

  const [step, setStep] = useState<Step>(1);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [cadence, setCadence] = useState<CadenceDraft>(INITIAL_CADENCE);
  const [timeoutSeconds, setTimeoutSeconds] = useState("3600");
  const [submitting, setSubmitting] = useState(false);
  const [committing, setCommitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmation, setConfirmation] = useState<{
    proposalId: string;
    token: string;
  } | null>(null);
  const commitStarted = useRef(false);

  const selected = ready.find((strategy) => strategy.id === selectedId) ?? null;
  const schedule = buildSchedule(cadence);
  const cadenceValid = isCadenceValid(schedule, cadence.mode);
  const timeout = Number(timeoutSeconds);
  const boundsValid =
    Number.isInteger(timeout) && timeout >= TIMEOUT_MIN && timeout <= TIMEOUT_MAX;

  const nextDisabled =
    step === 1 ? selected === null : step === 2 ? !cadenceValid : !boundsValid;

  async function submit() {
    if (!selected || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const body: AttachStrategyScheduleRequest = {
        schedule,
        timezone: timezone(),
        run_timeout_s: timeout,
      };
      const result = await api.createStrategySchedule(selected.id, body);
      if (result.requires_confirmation) {
        // The one-time token lives in component state only; nothing writes it
        // to localStorage or sessionStorage.
        setConfirmation({
          proposalId: result.proposal_id,
          token: result.confirm_token,
        });
      } else {
        onCreated(result.job_id);
      }
    } catch (submitError) {
      setError(
        submitError instanceof ApiError
          ? submitError.message
          : t("strategySchedules.actionFailed"),
      );
    } finally {
      setSubmitting(false);
    }
  }

  async function confirm() {
    if (!confirmation || commitStarted.current) return;
    commitStarted.current = true;
    setCommitting(true);
    setError(null);
    try {
      const row = await api.commitStrategySchedule(
        confirmation.proposalId,
        confirmation.token,
      );
      onCreated(row.job_id);
    } catch (commitError) {
      commitStarted.current = false;
      setError(
        commitError instanceof ApiError
          ? commitError.message
          : t("strategySchedules.actionFailed"),
      );
    } finally {
      setCommitting(false);
    }
  }

  function renderStep() {
    if (step === 1) {
      return (
        <StrategyStep
          ready={ready}
          excluded={excluded}
          selectedId={selectedId}
          onSelect={setSelectedId}
        />
      );
    }
    if (step === 2) {
      return (
        <CadenceStep
          draft={cadence}
          schedule={schedule}
          valid={cadenceValid}
          onChange={(patch) => setCadence((current) => ({ ...current, ...patch }))}
        />
      );
    }
    return (
      <OutputStep
        kind={selected?.kind}
        schedule={schedule}
        timeoutSeconds={timeoutSeconds}
        onTimeoutChange={setTimeoutSeconds}
      />
    );
  }

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="create-schedule-title"
        data-testid="create-schedule-dialog"
        onClick={(event) => event.stopPropagation()}
        className="flex max-h-[90vh] w-full max-w-lg flex-col rounded-lg border bg-background shadow-xl"
      >
        <header className="flex shrink-0 items-start justify-between gap-3 border-b px-4 py-3">
          <div className="min-w-0">
            <h2 id="create-schedule-title" className="text-sm font-semibold">
              {t("strategySchedules.modalTitle")}
            </h2>
            <p className="text-xs text-muted-foreground">
              {t("strategySchedules.stepCounter", { current: step, total: 3 })}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label={t("layout.cancel")}
            className="inline-flex items-center gap-1 rounded-md border px-2 py-1 text-xs text-muted-foreground transition hover:bg-muted"
          >
            <X className="h-3.5 w-3.5" aria-hidden />
          </button>
        </header>

        {confirmation ? (
          <div className="flex-1 overflow-y-auto p-4">
            <section
              data-testid="schedule-confirm-card"
              className="flex flex-col gap-3 rounded-md border border-warning/50 bg-warning/5 px-3 py-3"
            >
              <h3 className="text-sm font-semibold">
                {t("strategySchedules.confirmTitle")}
              </h3>
              <p className="text-xs text-muted-foreground">
                {t("strategySchedules.confirmBody")}
              </p>
              <dl className="text-xs">
                <dt className="text-muted-foreground">
                  {t("strategySchedules.proposalLabel")}
                </dt>
                <dd className="font-mono">{confirmation.proposalId}</dd>
              </dl>
              {error && (
                <p role="alert" className="text-xs text-danger">
                  {error}
                </p>
              )}
              <div className="flex items-center justify-end gap-1.5">
                <button
                  type="button"
                  onClick={onClose}
                  className="rounded-md border px-3 py-1 text-xs text-muted-foreground transition hover:bg-muted"
                >
                  {t("layout.cancel")}
                </button>
                <button
                  type="button"
                  onClick={() => void confirm()}
                  disabled={committing}
                  className="inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-1 text-xs font-medium text-primary-foreground transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {committing && (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
                  )}
                  {committing
                    ? t("strategySchedules.confirming")
                    : t("strategySchedules.confirmCreate")}
                </button>
              </div>
            </section>
          </div>
        ) : (
          <>
            <ol className="flex shrink-0 items-center gap-2 border-b px-4 py-2 text-xs">
              {STEP_KEYS.map((key, index) => {
                const number = index + 1;
                return (
                  <li
                    key={key}
                    aria-current={step === number ? "step" : undefined}
                    className={cn(
                      "inline-flex items-center gap-1",
                      step === number
                        ? "font-medium text-foreground"
                        : "text-muted-foreground",
                    )}
                  >
                    <span
                      className={cn(
                        "inline-flex h-4 w-4 items-center justify-center rounded-full text-[10px]",
                        step >= number
                          ? "bg-primary text-primary-foreground"
                          : "bg-muted text-muted-foreground",
                      )}
                    >
                      {number}
                    </span>
                    {t(key)}
                  </li>
                );
              })}
            </ol>

            <div className="flex-1 overflow-y-auto px-4 py-3">
              {renderStep()}
              {error && (
                <p role="alert" className="mt-3 text-xs text-danger">
                  {error}
                </p>
              )}
            </div>

            <footer className="flex shrink-0 items-center justify-between gap-2 border-t px-4 py-3">
              <button
                type="button"
                onClick={onClose}
                className="rounded-md border px-3 py-1 text-xs text-muted-foreground transition hover:bg-muted"
              >
                {t("layout.cancel")}
              </button>
              <div className="flex items-center gap-1.5">
                {step > 1 && (
                  <button
                    type="button"
                    onClick={() => setStep((step - 1) as Step)}
                    disabled={submitting}
                    className="rounded-md border px-3 py-1 text-xs text-muted-foreground transition hover:bg-muted disabled:opacity-50"
                  >
                    {t("strategySchedules.back")}
                  </button>
                )}
                {step < 3 ? (
                  <button
                    type="button"
                    onClick={() => setStep((step + 1) as Step)}
                    disabled={nextDisabled}
                    className="rounded-md bg-primary px-3 py-1 text-xs font-medium text-primary-foreground transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    {t("strategySchedules.next")}
                  </button>
                ) : (
                  <button
                    type="button"
                    onClick={() => void submit()}
                    disabled={nextDisabled || submitting}
                    className="inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-1 text-xs font-medium text-primary-foreground transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    {submitting && (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
                    )}
                    {submitting
                      ? t("strategySchedules.creating")
                      : t("strategySchedules.submit")}
                  </button>
                )}
              </div>
            </footer>
          </>
        )}
      </div>
    </div>,
    document.body,
  );
}