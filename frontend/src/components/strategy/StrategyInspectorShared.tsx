import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import type { StrategyDetail } from "@/lib/api";
import { cn } from "@/lib/utils";
import { runStatusKey, type RelativeTime } from "./labels";

export type InspectorTab =
  | "overview"
  | "contract"
  | "review"
  | "evidence"
  | "schedule"
  | "runs";

export interface PaneProps {
  detail: StrategyDetail;
}

export const RUN_TONE: Record<string, string> = {
  completed: "bg-success/10 text-success",
  failed: "bg-danger/10 text-danger",
  skipped: "bg-muted text-muted-foreground",
  missed: "bg-warning/10 text-warning",
  unknown: "bg-muted text-muted-foreground",
};

export const RUN_GLYPH: Record<string, string> = {
  completed: "●",
  failed: "▲",
  skipped: "–",
  missed: "○",
  unknown: "○",
};

export const MUST_PASS_CRITERIA = [
  { id: "goal_decidable", key: "strategyLibrary.criterionGoalDecidable" },
  { id: "inputs_complete", key: "strategyLibrary.criterionInputsComplete" },
  { id: "preconditions_checkable", key: "strategyLibrary.criterionPreconditionsCheckable" },
  { id: "steps_actionable", key: "strategyLibrary.criterionStepsActionable" },
  { id: "tools_allowlisted", key: "strategyLibrary.criterionToolsAllowlisted" },
  { id: "output_contract_parsable", key: "strategyLibrary.criterionOutputParsable" },
  { id: "failure_handling_explicit", key: "strategyLibrary.criterionFailureExplicit" },
  { id: "context_self_contained", key: "strategyLibrary.criterionContextSelfContained" },
] as const;

export function text(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

export function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

export function records(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value)
    ? value.map(record).filter((item): item is Record<string, unknown> => item !== null)
    : [];
}

export function strings(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string" && item.trim() !== "")
    : [];
}

export function numberText(value: unknown): string {
  return typeof value === "number" && Number.isFinite(value) ? value.toFixed(2) : "—";
}

export function verdictLabel(value: unknown): string | null {
  return typeof value === "string" && value !== "" ? value : null;
}

export type VerdictKey =
  | "strategyLibrary.verdictPass"
  | "strategyLibrary.verdictFail"
  | "strategyLibrary.verdictInconclusive"
  | "strategyLibrary.verdictUnknown";

export function verdictKey(verdict: string | null): VerdictKey {
  switch (verdict) {
    case "pass":
      return "strategyLibrary.verdictPass";
    case "fail":
      return "strategyLibrary.verdictFail";
    case "inconclusive":
      return "strategyLibrary.verdictInconclusive";
    default:
      return "strategyLibrary.verdictUnknown";
  }
}

export function Relative({ value }: { value: RelativeTime | null }) {
  const { t } = useTranslation();
  if (!value) return <span>—</span>;
  return <>{t(value.key, { count: value.count })}</>;
}

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

export function Section({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <section className="flex flex-col gap-1.5">
      <h3 className="text-xs font-semibold text-muted-foreground">{title}</h3>
      {children}
    </section>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="text-xs text-muted-foreground">{children}</p>;
}

export function DefinitionRow({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-3 border-b border-border/40 py-1 text-xs last:border-0">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="text-right font-medium">{value}</dd>
    </div>
  );
}

export function DenseTable({
  headers,
  children,
}: {
  headers: string[];
  children: ReactNode;
}) {
  return (
    <div className="overflow-x-auto rounded-md border bg-card">
      <table className="w-full text-[13px]">
        <thead>
          <tr className="border-b bg-muted/40 text-left text-xs text-muted-foreground">
            {headers.map((header) => (
              <th key={header} className="px-3 py-1.5 font-medium">
                {header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}
