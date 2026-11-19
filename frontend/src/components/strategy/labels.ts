import type { StrategyEvidenceQuality, StrategyKind, StrategyReadiness } from "@/lib/api";

export function kindKey(kind: StrategyKind) {
  return kind === "workflow" ? "strategyLibrary.kindWorkflow" : "strategyLibrary.kindCode";
}

export function readinessKey(readiness: StrategyReadiness) {
  switch (readiness) {
    case "draft":
      return "strategyLibrary.readinessDraft";
    case "needs_work":
      return "strategyLibrary.readinessNeedsWork";
    case "reviewing":
      return "strategyLibrary.readinessReviewing";
    case "ready":
      return "strategyLibrary.readinessReady";
    case "rejected":
      return "strategyLibrary.readinessRejected";
  }
}

export function qualityKey(quality: StrategyEvidenceQuality | null) {
  switch (quality) {
    case "high":
      return "strategyLibrary.qualityHigh";
    case "medium":
      return "strategyLibrary.qualityMedium";
    case "low":
      return "strategyLibrary.qualityLow";
    case "stale":
      return "strategyLibrary.qualityStale";
    default:
      return "strategyLibrary.qualityNone";
  }
}

export function runStatusKey(status: string) {
  switch (status) {
    case "completed":
      return "strategyLibrary.lastRunCompleted";
    case "failed":
      return "strategyLibrary.lastRunFailed";
    case "skipped":
      return "strategyLibrary.lastRunSkipped";
    case "missed":
      return "strategyLibrary.lastRunMissed";
    default:
      return "strategyLibrary.lastRunUnknown";
  }
}

export type RelativeTimeKey =
  | "strategyLibrary.timeJustNow"
  | "strategyLibrary.timeMinutesAgo"
  | "strategyLibrary.timeHoursAgo"
  | "strategyLibrary.timeDaysAgo";

export interface RelativeTime {
  key: RelativeTimeKey;
  count?: number;
}

/** Relative label for an ISO timestamp, or null when it cannot be parsed. */
export function relativeTime(iso: string | null, now = Date.now()): RelativeTime | null {
  if (!iso) return null;
  const value = Date.parse(iso);
  if (!Number.isFinite(value)) return null;
  return relativeTimeFromEpoch(value, now);
}

export function relativeTimeFromEpoch(epochMs: number, now = Date.now()): RelativeTime {
  const minutes = Math.floor(Math.max(0, now - epochMs) / 60_000);
  if (minutes < 1) return { key: "strategyLibrary.timeJustNow" };
  if (minutes < 60) return { key: "strategyLibrary.timeMinutesAgo", count: minutes };
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return { key: "strategyLibrary.timeHoursAgo", count: hours };
  return { key: "strategyLibrary.timeDaysAgo", count: Math.floor(hours / 24) };
}