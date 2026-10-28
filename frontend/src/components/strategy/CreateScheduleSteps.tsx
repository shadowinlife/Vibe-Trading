import { useTranslation } from "react-i18next";
import type { StrategyKind, StrategySummary } from "@/lib/api";
import { formatWeekdays } from "@/lib/cadence";
import { cn } from "@/lib/utils";
import { cadenceLabel } from "./ScheduleTable";
import { kindKey } from "./labels";

export type CadenceMode = "daily" | "weekly" | "interval" | "custom";

export interface CadenceDraft {
  mode: CadenceMode;
  time: string;
  weekdays: number[];
  intervalMinutes: string;
  custom: string;
}

export const STEP_KEYS = [
  "strategySchedules.stepStrategy",
  "strategySchedules.stepCadence",
  "strategySchedules.stepOutput",
] as const;

export const TIMEOUT_MIN = 30;
export const TIMEOUT_MAX = 86_400;

const MODES: Array<{
  id: CadenceMode;
  key:
    | "strategySchedules.cadencePresetDaily"
    | "strategySchedules.cadencePresetWeekly"
    | "strategySchedules.cadencePresetInterval"
    | "strategySchedules.cadencePresetCustom";
}> = [
  { id: "daily", key: "strategySchedules.cadencePresetDaily" },
  { id: "weekly", key: "strategySchedules.cadencePresetWeekly" },
  { id: "interval", key: "strategySchedules.cadencePresetInterval" },
  { id: "custom", key: "strategySchedules.cadencePresetCustom" },
];

const WEEKDAYS = [1, 2, 3, 4, 5, 6, 0];

function parseTime(value: string): [number, number] | null {
  const match = /^(\d{1,2}):(\d{2})$/.exec(value);
  if (!match) return null;
  const hour = Number(match[1]);
  const minute = Number(match[2]);
  if (hour > 23 || minute > 59) return null;
  return [hour, minute];
}

export function buildSchedule(draft: CadenceDraft): string {
  const parsed = parseTime(draft.time);
  if (draft.mode === "daily") {
    return parsed ? `${parsed[1]} ${parsed[0]} * * *` : "";
  }
  if (draft.mode === "weekly") {
    if (!parsed || draft.weekdays.length === 0) return "";
    const days = [...draft.weekdays].sort((a, b) => a - b).join(",");
    return `${parsed[1]} ${parsed[0]} * * ${days}`;
  }
  if (draft.mode === "interval") {
    const minutes = Number(draft.intervalMinutes);
    if (!Number.isInteger(minutes) || minutes < 1) return "";
    return String(minutes * 60_000);
  }
  return draft.custom.trim();
}

export function isCadenceValid(schedule: string, mode: CadenceMode): boolean {
  if (schedule.length === 0) return false;
  if (mode !== "custom") return true;
  // A custom expression is any five-field cron; describeCadence may still
  // recognize it as a daily or weekly cadence, which is a preview concern.
  return schedule.split(/\s+/).length === 5;
}

interface StrategyStepProps {
  ready: StrategySummary[];
  excluded: number;
  selectedId: string | null;
  onSelect: (strategyId: string) => void;
}

export function StrategyStep({
  ready,
  excluded,
  selectedId,
  onSelect,
}: StrategyStepProps) {
  const { t } = useTranslation();
  return (
    <div data-testid="schedule-step-1" className="flex flex-col gap-3">
      <p className="text-xs text-muted-foreground">
        {t("strategySchedules.readyOnlyNote")}
      </p>
      {ready.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          {t("strategySchedules.noReady")}
        </p>
      ) : (
        <div
          role="radiogroup"
          aria-label={t("strategySchedules.stepStrategy")}
          className="flex flex-col gap-1.5"
        >
          {ready.map((strategy) => (
            <label
              key={strategy.id}
              className={cn(
                "flex cursor-pointer items-center gap-3 rounded-md border px-3 py-2 text-sm transition",
                selectedId === strategy.id
                  ? "border-primary bg-primary/5"
                  : "hover:bg-muted/50",
              )}
            >
              <input
                type="radio"
                name="strategy"
                value={strategy.id}
                checked={selectedId === strategy.id}
                onChange={() => onSelect(strategy.id)}
              />
              <span className="min-w-0 flex-1 truncate font-medium">
                {strategy.name}
              </span>
              <span className="text-xs text-muted-foreground">
                {t(kindKey(strategy.kind))}
              </span>
              <span className="font-mono text-[11px] text-muted-foreground">
                {t("strategyLibrary.inspectorVersion", { version: strategy.version })}
              </span>
            </label>
          ))}
        </div>
      )}
      {excluded > 0 && (
        <p className="text-xs text-muted-foreground">
          {t("strategySchedules.excludedNote", { count: excluded })}
        </p>
      )}
    </div>
  );
}

interface CadenceStepProps {
  draft: CadenceDraft;
  schedule: string;
  valid: boolean;
  onChange: (patch: Partial<CadenceDraft>) => void;
}

export function CadenceStep({ draft, schedule, valid, onChange }: CadenceStepProps) {
  const { t, i18n } = useTranslation();
  const locale = i18n.language;

  function toggleWeekday(day: number) {
    onChange({
      weekdays: draft.weekdays.includes(day)
        ? draft.weekdays.filter((value) => value !== day)
        : [...draft.weekdays, day],
    });
  }

  return (
    <div data-testid="schedule-step-2" className="flex flex-col gap-3">
      <div
        role="radiogroup"
        aria-label={t("strategySchedules.stepCadence")}
        className="flex flex-wrap gap-1.5"
      >
        {MODES.map((entry) => (
          <label
            key={entry.id}
            className={cn(
              "cursor-pointer rounded-md border px-2.5 py-1 text-xs transition",
              draft.mode === entry.id
                ? "border-primary bg-primary/5 font-medium"
                : "text-muted-foreground hover:bg-muted",
            )}
          >
            <input
              type="radio"
              name="cadence-mode"
              value={entry.id}
              checked={draft.mode === entry.id}
              onChange={() => onChange({ mode: entry.id })}
              className="sr-only"
            />
            {t(entry.key)}
          </label>
        ))}
      </div>

      {draft.mode === "interval" ? (
        <label className="flex flex-col gap-1 text-xs">
          <span className="text-muted-foreground">
            {t("strategySchedules.intervalMinutes")}
          </span>
          <input
            type="number"
            min={1}
            step={1}
            value={draft.intervalMinutes}
            onChange={(event) => onChange({ intervalMinutes: event.target.value })}
            aria-label={t("strategySchedules.intervalMinutes")}
            className="w-32 rounded-md border bg-background px-2 py-1.5 text-sm outline-none transition focus:border-primary"
          />
        </label>
      ) : draft.mode === "custom" ? (
        <label className="flex flex-col gap-1 text-xs">
          <span className="text-muted-foreground">
            {t("strategySchedules.expressionLabel")}
          </span>
          <input
            type="text"
            value={draft.custom}
            onChange={(event) => onChange({ custom: event.target.value })}
            aria-label={t("strategySchedules.expressionLabel")}
            className="rounded-md border bg-background px-2 py-1.5 font-mono text-sm outline-none transition focus:border-primary"
          />
        </label>
      ) : (
        <label className="flex flex-col gap-1 text-xs">
          <span className="text-muted-foreground">
            {t("strategySchedules.timeLabel")}
          </span>
          <input
            type="time"
            value={draft.time}
            onChange={(event) => onChange({ time: event.target.value })}
            aria-label={t("strategySchedules.timeLabel")}
            className="w-32 rounded-md border bg-background px-2 py-1.5 text-sm outline-none transition focus:border-primary"
          />
        </label>
      )}

      {draft.mode === "weekly" && (
        <fieldset className="flex flex-col gap-1 text-xs">
          <legend className="mb-1 text-muted-foreground">
            {t("strategySchedules.weekdaysLabel")}
          </legend>
          <div className="flex flex-wrap gap-2">
            {WEEKDAYS.map((day) => (
              <label key={day} className="inline-flex items-center gap-1">
                <input
                  type="checkbox"
                  checked={draft.weekdays.includes(day)}
                  onChange={() => toggleWeekday(day)}
                />
                {formatWeekdays([day], locale)}
              </label>
            ))}
          </div>
        </fieldset>
      )}

      <p className="text-xs text-muted-foreground">
        {t("strategySchedules.timezoneLabel")}: {timezone()}
      </p>
      <p
        data-testid="schedule-cadence-preview"
        className={cn("text-xs", valid ? "text-foreground" : "text-danger")}
      >
        {valid
          ? t("strategySchedules.preview", {
              cadence: cadenceLabel(schedule, t, locale),
            })
          : t("strategySchedules.invalidCadence")}
      </p>
    </div>
  );
}

interface OutputStepProps {
  kind: StrategyKind | undefined;
  schedule: string;
  timeoutSeconds: string;
  onTimeoutChange: (value: string) => void;
}

export function OutputStep({
  kind,
  schedule,
  timeoutSeconds,
  onTimeoutChange,
}: OutputStepProps) {
  const { t, i18n } = useTranslation();
  return (
    <div data-testid="schedule-step-3" className="flex flex-col gap-3">
      <dl className="rounded-md border bg-card px-3 py-2 text-xs">
        <div className="flex items-center justify-between gap-3 py-0.5">
          <dt className="text-muted-foreground">
            {t("strategySchedules.outputTitle")}
          </dt>
          <dd className="text-right font-medium">
            {t(
              kind === "code"
                ? "strategySchedules.outputCode"
                : "strategySchedules.outputWorkflow",
            )}
          </dd>
        </div>
        <div className="flex items-center justify-between gap-3 py-0.5">
          <dt className="text-muted-foreground">
            {t("strategySchedules.columnsCadence")}
          </dt>
          <dd className="text-right">
            {cadenceLabel(schedule, t, i18n.language)}
          </dd>
        </div>
        <div className="flex items-center justify-between gap-3 py-0.5">
          <dt className="text-muted-foreground">
            {t("strategySchedules.timezoneLabel")}
          </dt>
          <dd className="text-right font-mono">{timezone()}</dd>
        </div>
      </dl>

      <label className="flex flex-col gap-1 text-xs">
        <span className="text-muted-foreground">
          {t("strategySchedules.timeoutLabel")}
        </span>
        <input
          type="number"
          min={TIMEOUT_MIN}
          max={TIMEOUT_MAX}
          step={1}
          value={timeoutSeconds}
          onChange={(event) => onTimeoutChange(event.target.value)}
          aria-label={t("strategySchedules.timeoutLabel")}
          className="w-40 rounded-md border bg-background px-2 py-1.5 text-sm outline-none transition focus:border-primary"
        />
        <span className="text-muted-foreground">
          {t("strategySchedules.timeoutHint", {
            min: TIMEOUT_MIN,
            max: TIMEOUT_MAX,
          })}
        </span>
      </label>
    </div>
  );
}

export function timezone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
}