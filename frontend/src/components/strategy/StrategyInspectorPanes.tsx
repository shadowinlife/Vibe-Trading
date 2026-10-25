import { useTranslation } from "react-i18next";
import { readinessKey, relativeTimeFromEpoch } from "./labels";
import {
  DefinitionRow,
  DenseTable,
  Empty,
  Relative,
  RunBadge,
  Section,
  record,
  records,
  strings,
  text,
  type PaneProps,
} from "./StrategyInspectorShared";

export type { PaneProps };

export function OverviewPane({ detail }: PaneProps) {
  const { t } = useTranslation();
  const thesis = text(record(detail.spec)?.thesis);
  const lastRun = detail.last_run;
  const nextRun =
    detail.schedule.next_run_at === null
      ? null
      : relativeTimeFromEpoch(detail.schedule.next_run_at);

  return (
    <div className="flex flex-col gap-4">
      <Section title={t("strategyLibrary.inspectorLabelWhatItDoes")}>
        {thesis ? (
          <p className="text-sm">{thesis}</p>
        ) : (
          <Empty>{t("strategyLibrary.inspectorNoThesis")}</Empty>
        )}
      </Section>
      <dl className="rounded-md border bg-card px-3 py-1">
        <DefinitionRow
          label={t("strategyLibrary.inspectorLabelReadiness")}
          value={t(readinessKey(detail.readiness))}
        />
        <DefinitionRow
          label={t("strategyLibrary.inspectorLabelVersion")}
          value={<span className="font-mono">{detail.version || "—"}</span>}
        />
        <DefinitionRow
          label={t("strategyLibrary.inspectorLabelUniverse")}
          value={detail.universe || "—"}
        />
        <DefinitionRow
          label={t("strategyLibrary.inspectorLabelDemo")}
          value={
            detail.demo
              ? t("strategyLibrary.inspectorDemoYes")
              : t("strategyLibrary.inspectorDemoNo")
          }
        />
        <DefinitionRow
          label={t("strategyLibrary.inspectorLabelScheduleCount")}
          value={<span className="tabular-nums">{detail.schedule.count}</span>}
        />
        <DefinitionRow
          label={t("strategyLibrary.inspectorLabelLastRun")}
          value={
            lastRun ? (
              <span className="inline-flex items-center gap-1.5">
                <RunBadge status={lastRun.status} />
                {lastRun.finished_at !== null && (
                  <Relative value={relativeTimeFromEpoch(lastRun.finished_at)} />
                )}
              </span>
            ) : (
              t("strategyLibrary.lastRunNone")
            )
          }
        />
        <DefinitionRow
          label={t("strategyLibrary.scheduleNextRun")}
          value={nextRun ? <Relative value={nextRun} /> : t("strategyLibrary.scheduleNoNext")}
        />
      </dl>
    </div>
  );
}

export function ContractPane({ detail }: PaneProps) {
  const { t } = useTranslation();
  const spec = record(detail.spec) ?? {};
  const inputs = records(spec.inputs);
  const steps = records(spec.steps);
  const preconditions = strings(spec.preconditions);
  const failure = text(spec.failure_handling);
  const output = record(spec.output_contract);
  const outputFields = strings(output?.required_fields);
  const tools = [...new Set(steps.map((step) => text(step.tool)).filter(Boolean))];

  return (
    <div className="flex flex-col gap-5">
      <Section title={t("strategyLibrary.contractInputs")}>
        {inputs.length === 0 ? (
          <Empty>{t("strategyLibrary.contractEmpty")}</Empty>
        ) : (
          <DenseTable
            headers={[
              t("strategyLibrary.contractInputName"),
              t("strategyLibrary.contractInputSource"),
              t("strategyLibrary.contractInputRequirement"),
              t("strategyLibrary.contractInputMissingPolicy"),
            ]}
          >
            {inputs.map((input, index) => (
              <tr key={`${text(input.name)}-${index}`} className="border-b border-border/40 last:border-0">
                <td className="px-3 py-1 font-medium">{text(input.name) || "—"}</td>
                <td className="px-3 py-1 text-muted-foreground">{text(input.source) || "—"}</td>
                <td className="px-3 py-1 text-muted-foreground">
                  {input.required === false
                    ? t("strategyLibrary.contractInputOptional")
                    : t("strategyLibrary.contractInputRequired")}
                </td>
                <td className="px-3 py-1 text-muted-foreground">
                  {text(input.missing_policy) || "—"}
                </td>
              </tr>
            ))}
          </DenseTable>
        )}
      </Section>

      <Section title={t("strategyLibrary.contractSteps")}>
        {steps.length === 0 ? (
          <Empty>{t("strategyLibrary.contractEmpty")}</Empty>
        ) : (
          <DenseTable
            headers={[
              t("strategyLibrary.contractStepId"),
              t("strategyLibrary.contractStepAction"),
              t("strategyLibrary.contractStepTool"),
              t("strategyLibrary.contractStepOutput"),
            ]}
          >
            {steps.map((step, index) => (
              <tr key={`${text(step.id)}-${index}`} className="border-b border-border/40 last:border-0">
                <td className="px-3 py-1 font-mono text-xs">{text(step.id) || "—"}</td>
                <td className="px-3 py-1">{text(step.action) || "—"}</td>
                <td className="px-3 py-1 font-mono text-xs text-muted-foreground">
                  {text(step.tool) || "—"}
                </td>
                <td className="px-3 py-1 text-muted-foreground">{text(step.output) || "—"}</td>
              </tr>
            ))}
          </DenseTable>
        )}
      </Section>

      <Section title={t("strategyLibrary.contractTools")}>
        {tools.length === 0 ? (
          <Empty>{t("strategyLibrary.contractEmpty")}</Empty>
        ) : (
          <div className="flex flex-wrap gap-1">
            {tools.map((tool) => (
              <code key={tool} className="rounded bg-muted px-1.5 py-0.5 text-xs">
                {tool}
              </code>
            ))}
          </div>
        )}
      </Section>

      <Section title={t("strategyLibrary.contractOutput")}>
        {output === null || Object.keys(output).length === 0 ? (
          <Empty>{t("strategyLibrary.contractOutputNone")}</Empty>
        ) : (
          <dl className="rounded-md border bg-card px-3 py-1">
            <DefinitionRow
              label={t("strategyLibrary.contractOutputFormat")}
              value={text(output.format) || "—"}
            />
            <DefinitionRow
              label={t("strategyLibrary.contractOutputFields")}
              value={outputFields.length > 0 ? outputFields.join(", ") : "—"}
            />
            <DefinitionRow
              label={t("strategyLibrary.contractOutputDestination")}
              value={text(output.destination) || "—"}
            />
          </dl>
        )}
      </Section>

      <Section title={t("strategyLibrary.contractPreconditions")}>
        {preconditions.length === 0 ? (
          <Empty>{t("strategyLibrary.contractEmpty")}</Empty>
        ) : (
          <ul className="list-disc space-y-0.5 ps-4 text-xs">
            {preconditions.map((item, index) => (
              <li key={`${item}-${index}`}>{item}</li>
            ))}
          </ul>
        )}
      </Section>

      <Section title={t("strategyLibrary.contractFailure")}>
        {failure ? (
          <p className="text-xs">{failure}</p>
        ) : (
          <Empty>{t("strategyLibrary.contractNotDeclared")}</Empty>
        )}
      </Section>

      {detail.kind === "code" && (
        <>
          <Section title={t("strategyLibrary.contractCodeConfig")}>
            <dl className="rounded-md border bg-card px-3 py-1">
              <DefinitionRow
                label={t("strategyLibrary.contractConfigKeys")}
                value={<span className="tabular-nums">{detail.config_summary.keys}</span>}
              />
              <DefinitionRow
                label={t("strategyLibrary.inspectorLabelSource")}
                value={detail.config_summary.source || "—"}
              />
              <DefinitionRow
                label={t("strategyLibrary.inspectorLabelUniverse")}
                value={detail.config_summary.universe || "—"}
              />
            </dl>
          </Section>
          <Section title={t("strategyLibrary.contractSignalEngine")}>
            {detail.signal_engine_path ? (
              <code className="block break-all rounded bg-muted px-2 py-1 text-xs">
                {detail.signal_engine_path}
              </code>
            ) : (
              <Empty>{t("strategyLibrary.contractSignalEngineNone")}</Empty>
            )}
          </Section>
        </>
      )}
    </div>
  );
}
