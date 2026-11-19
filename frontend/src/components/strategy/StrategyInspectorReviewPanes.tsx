import { useTranslation } from "react-i18next";
import type { StrategyGap, StrategyRun } from "@/lib/api";
import { qualityKey, relativeTime, relativeTimeFromEpoch } from "./labels";
import {
  DenseTable,
  DefinitionRow,
  Empty,
  MUST_PASS_CRITERIA,
  Relative,
  RunBadge,
  Section,
  numberText,
  record,
  records,
  text,
  verdictKey,
  verdictLabel,
  type PaneProps,
} from "./StrategyInspectorShared";

export type { PaneProps };

export function ReviewPane({ detail }: PaneProps) {
  const { t } = useTranslation();
  const verdict = record(detail.evaluator_verdict);
  const previous = record(detail.previous_verdict);
  const mustPass = records(verdict?.must_pass);
  const current = verdictLabel(verdict?.verdict);
  const previousVerdict = verdictLabel(previous?.verdict);
  const evaluatedAt = relativeTime(text(verdict?.created_at));

  return (
    <div className="flex flex-col gap-5">
      <Section title={t("strategyLibrary.reviewMustPass")}>
        <div className="overflow-x-auto rounded-md border bg-card">
          <table className="w-full text-[13px]">
            <tbody>
              {MUST_PASS_CRITERIA.map((criterion) => {
                const row = mustPass.find((item) => text(item.id) === criterion.id);
                const passed = row?.pass === true;
                return (
                  <tr
                    key={criterion.id}
                    data-testid={`inspector-check-${criterion.id}`}
                    className="border-b border-border/40 last:border-0"
                  >
                    <td className="w-8 px-3 py-1">
                      <span
                        aria-hidden
                        className={passed ? "text-success" : "text-muted-foreground"}
                      >
                        {row ? (passed ? "✓" : "✗") : "·"}
                      </span>
                    </td>
                    <td className="px-3 py-1">{t(criterion.key)}</td>
                    <td className="px-3 py-1 text-xs text-muted-foreground">
                      {row
                        ? passed
                          ? t("strategyLibrary.reviewPass")
                          : t("strategyLibrary.reviewFail")
                        : t("strategyLibrary.reviewNotEvaluated")}
                      {row && text(row.why) ? ` — ${text(row.why)}` : ""}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </Section>

      <Section title={t("strategyLibrary.reviewGaps")}>
        {detail.gaps.length === 0 ? (
          <Empty>{t("strategyLibrary.reviewNoGaps")}</Empty>
        ) : (
          <ul className="flex flex-col gap-1.5">
            {detail.gaps.map((gap: StrategyGap, index) => (
              <li
                key={`${gap.code}-${index}`}
                data-testid="inspector-gap"
                className="rounded-md border border-warning/40 bg-warning/5 px-2.5 py-1.5 text-xs"
              >
                <div className="flex flex-wrap items-baseline gap-2">
                  <code className="rounded bg-warning/10 px-1 py-0.5 font-mono text-[11px] text-warning">
                    {gap.code || "—"}
                  </code>
                  {gap.message && <span>{gap.message}</span>}
                </div>
                {gap.field && (
                  <p className="mt-0.5 font-mono text-[11px] text-muted-foreground">{gap.field}</p>
                )}
                {gap.fix_hint && (
                  <p className="mt-0.5 text-muted-foreground">
                    {t("strategyLibrary.reviewGapFix", { hint: gap.fix_hint })}
                  </p>
                )}
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section title={t("strategyLibrary.reviewCurrentVerdict")}>
        {current ? (
          <div className="rounded-md border bg-card px-3 py-2 text-xs">
            <p className="font-semibold">{t(verdictKey(current))}</p>
            {text(verdict?.summary) && (
              <p className="mt-1 text-muted-foreground">{text(verdict?.summary)}</p>
            )}
            {evaluatedAt && (
              <p className="mt-1 text-muted-foreground">
                {t("strategyLibrary.reviewEvaluatedAt", {
                  when: t(evaluatedAt.key, { count: evaluatedAt.count }),
                })}
              </p>
            )}
          </div>
        ) : (
          <Empty>{t("strategyLibrary.reviewNoVerdict")}</Empty>
        )}
      </Section>

      <Section title={t("strategyLibrary.reviewPreviousVerdict")}>
        <p className="text-[11px] text-muted-foreground">
          {t("strategyLibrary.reviewPreviousHint")}
        </p>
        {previousVerdict ? (
          <div className="rounded-md border bg-card px-3 py-2 text-xs">
            <p className="font-semibold">{t(verdictKey(previousVerdict))}</p>
            {text(previous?.artifact_version) && (
              <p className="mt-1 font-mono text-muted-foreground">
                v{text(previous?.artifact_version)}
              </p>
            )}
          </div>
        ) : (
          <Empty>{t("strategyLibrary.reviewNoPrevious")}</Empty>
        )}
      </Section>
    </div>
  );
}

export function EvidencePane({ detail }: PaneProps) {
  const { t } = useTranslation();
  const verified = relativeTime(detail.evidence.last_verified);
  const bench = detail.bench_history;
  const latest = bench[0];

  return (
    <div className="flex flex-col gap-5">
      <Section title={t("strategyLibrary.evidenceFreshness")}>
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <span className="inline-flex rounded bg-muted px-1.5 py-0.5 font-medium">
            {t(qualityKey(detail.evidence.quality))}
          </span>
          <span className="text-muted-foreground">
            {verified
              ? t("strategyLibrary.evidenceVerifiedAgo", {
                  when: t(verified.key, { count: verified.count }),
                })
              : t("strategyLibrary.evidenceNeverVerified")}
          </span>
          {detail.evidence.quality === "stale" && (
            <span className="rounded bg-warning/10 px-1.5 py-0.5 font-medium text-warning">
              {t("strategyLibrary.evidenceStaleFlag")}
            </span>
          )}
        </div>
      </Section>

      <Section title={t("strategyLibrary.evidenceLatestBacktest")}>
        {latest ? (
          <dl className="rounded-md border bg-card px-3 py-1">
            <DefinitionRow
              label={t("strategyLibrary.evidenceBenchCreated")}
              value={<Relative value={relativeTime(latest.created_at)} />}
            />
            <DefinitionRow
              label={t("strategyLibrary.evidenceBenchSharpe")}
              value={numberText(latest.sharpe)}
            />
            <DefinitionRow
              label={t("strategyLibrary.evidenceBenchReturn")}
              value={numberText(latest.annual_return)}
            />
            <DefinitionRow
              label={t("strategyLibrary.evidenceBenchDrawdown")}
              value={numberText(latest.max_drawdown)}
            />
            <DefinitionRow
              label={t("strategyLibrary.evidenceBenchCalmar")}
              value={numberText(latest.calmar)}
            />
            <DefinitionRow
              label={t("strategyLibrary.evidenceRunDir")}
              value={<span className="break-all font-mono text-[11px]">{latest.run_dir || "—"}</span>}
            />
          </dl>
        ) : (
          <Empty>{t("strategyLibrary.evidenceNoBacktest")}</Empty>
        )}
      </Section>

      <Section title={t("strategyLibrary.evidenceBenchHistory")}>
        {bench.length === 0 ? (
          <Empty>{t("strategyLibrary.evidenceNoBench")}</Empty>
        ) : (
          <DenseTable
            headers={[
              t("strategyLibrary.evidenceBenchType"),
              t("strategyLibrary.evidenceBenchSharpe"),
              t("strategyLibrary.evidenceBenchReturn"),
              t("strategyLibrary.evidenceBenchDrawdown"),
              t("strategyLibrary.evidenceBenchCreated"),
            ]}
          >
            {bench.map((row) => (
              <tr key={row.id} className="border-b border-border/40 last:border-0">
                <td className="px-3 py-1">{row.bench_type || "—"}</td>
                <td className="px-3 py-1 tabular-nums">{numberText(row.sharpe)}</td>
                <td className="px-3 py-1 tabular-nums">{numberText(row.annual_return)}</td>
                <td className="px-3 py-1 tabular-nums">{numberText(row.max_drawdown)}</td>
                <td className="px-3 py-1 text-muted-foreground">
                  <Relative value={relativeTime(row.created_at)} />
                </td>
              </tr>
            ))}
          </DenseTable>
        )}
      </Section>
    </div>
  );
}

export function SchedulePane({ detail }: PaneProps) {
  const { t } = useTranslation();
  const nextRun =
    detail.schedule.next_run_at === null
      ? null
      : relativeTimeFromEpoch(detail.schedule.next_run_at);
  const recent = detail.schedules
    .flatMap((schedule) => schedule.last_runs)
    .sort((a, b) => b.started_at - a.started_at)
    .slice(0, 5);

  return (
    <div className="flex flex-col gap-5">
      <dl className="rounded-md border bg-card px-3 py-1">
        <DefinitionRow
          label={t("strategyLibrary.scheduleActiveCount")}
          value={<span className="tabular-nums">{detail.schedule.count}</span>}
        />
        <DefinitionRow
          label={t("strategyLibrary.scheduleNextRun")}
          value={nextRun ? <Relative value={nextRun} /> : t("strategyLibrary.scheduleNoNext")}
        />
      </dl>

      <Section title={t("strategyLibrary.scheduleRecentRuns")}>
        {recent.length === 0 ? (
          <Empty>
            {detail.schedules.length === 0
              ? t("strategyLibrary.scheduleNone")
              : t("strategyLibrary.scheduleNoRuns")}
          </Empty>
        ) : (
          <ul className="flex flex-col gap-1">
            {recent.map((run) => (
              <li
                key={run.run_id}
                data-testid="inspector-scheduled-run"
                className="flex items-center justify-between gap-2 rounded-md border bg-card px-2.5 py-1 text-xs"
              >
                <RunBadge status={run.status} />
                <span className="text-muted-foreground">
                  <Relative value={relativeTimeFromEpoch(run.started_at)} />
                </span>
              </li>
            ))}
          </ul>
        )}
      </Section>
    </div>
  );
}

export function RunsPane({ detail }: PaneProps) {
  const { t } = useTranslation();
  const runs: StrategyRun[] = detail.run_history.slice(0, 20);

  return (
    <Section title={t("strategyLibrary.runsRecent")}>
      {runs.length === 0 ? (
        <Empty>{t("strategyLibrary.runsEmpty")}</Empty>
      ) : (
        <ul className="flex flex-col gap-1.5">
          {runs.map((run) => (
            <li
              key={run.run_id}
              data-testid="inspector-run"
              className="rounded-md border bg-card px-2.5 py-1.5 text-xs"
            >
              <div className="flex flex-wrap items-center gap-2">
                <RunBadge status={run.status} />
                <span className="text-muted-foreground">
                  <Relative value={relativeTimeFromEpoch(run.started_at)} />
                </span>
                {run.strategy_version && (
                  <span className="font-mono text-[11px] text-muted-foreground">
                    v{run.strategy_version}
                  </span>
                )}
              </div>
              {run.summary && <p className="mt-1 text-muted-foreground">{run.summary}</p>}
              {(run.error || (run.status === "failed" && run.error_kind)) && (
                <p className="mt-1 text-danger">
                  {t("strategyLibrary.runsError")}: {run.error || run.error_kind}
                </p>
              )}
            </li>
          ))}
        </ul>
      )}
    </Section>
  );
}
