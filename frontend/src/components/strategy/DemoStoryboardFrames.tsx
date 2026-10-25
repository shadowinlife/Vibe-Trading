import { useTranslation } from "react-i18next";
import { Link } from "react-router";
import { cadenceLabel } from "./ScheduleTable";
import { RunBadge } from "./ScheduleRuns";
import { MUST_PASS_CRITERIA } from "./StrategyInspectorShared";
import { readinessKey } from "./labels";
import { DEMO_FRAMES } from "./demoScript";

export const LAST_INDEX = DEMO_FRAMES.length - 1;

export const BUTTON_CLASS =
  "rounded-md border px-2 py-1 text-xs text-muted-foreground transition hover:bg-muted";
export const PILL_CLASS = "inline-flex rounded px-1.5 py-0.5 text-[11px] font-medium";

export function SolidifyPreview() {
  const { t } = useTranslation();
  return (
    <div className="overflow-x-auto rounded-md border bg-card">
      <table className="w-full text-[11px]">
        <tbody>
          <tr className="border-b border-border/40">
            <td className="px-2 py-1.5 font-medium">
              {t("strategyLibrary.demoStrategyName")}
            </td>
            <td className="px-2 py-1.5">
              <span className={`${PILL_CLASS} bg-success/10 text-success`}>
                {t(readinessKey("ready"))}
              </span>
            </td>
            <td className="px-2 py-1.5 text-muted-foreground">
              {t("strategyLibrary.inspectorVersion", { version: "1.0.0" })}
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}

export function GapsPreview() {
  const { t } = useTranslation();
  return (
    <div className="rounded-md border border-warning/40 bg-warning/5 px-2.5 py-1.5 text-xs">
      <div className="flex flex-wrap items-baseline gap-2">
        <code className="rounded bg-warning/10 px-1 py-0.5 font-mono text-[11px] text-warning">
          missing_preconditions
        </code>
        <span>{t("strategyLibrary.reviewGapFix", { hint: t("strategyLibrary.demoGapHint") })}</span>
      </div>
      <p className="mt-0.5 font-mono text-[11px] text-muted-foreground">
        spec.preconditions
      </p>
    </div>
  );
}

export function ReviewPreview() {
  const { t } = useTranslation();
  return (
    <div className="overflow-x-auto rounded-md border bg-card">
      <table className="w-full text-[11px]">
        <tbody>
          {MUST_PASS_CRITERIA.map((criterion) => (
            <tr key={criterion.id} className="border-b border-border/40 last:border-0">
              <td className="w-6 px-2 py-0.5 text-success" aria-hidden>
                ✓
              </td>
              <td className="px-2 py-0.5">{t(criterion.key)}</td>
              <td className="px-2 py-0.5 text-muted-foreground">
                {t("strategyLibrary.reviewPass")}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function SchedulePreview() {
  const { t, i18n } = useTranslation();
  return (
    <div className="overflow-x-auto rounded-md border bg-card">
      <table className="w-full text-[11px]">
        <thead>
          <tr className="border-b bg-muted/40 text-left text-muted-foreground">
            <th className="px-2 py-1 font-medium">
              {t("strategySchedules.columnsStrategy")}
            </th>
            <th className="px-2 py-1 font-medium">
              {t("strategySchedules.columnsCadence")}
            </th>
            <th className="px-2 py-1 font-medium">
              {t("strategySchedules.columnsStatus")}
            </th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td className="px-2 py-1.5 font-medium">
              {t("strategyLibrary.demoStrategyName")}
            </td>
            <td className="px-2 py-1.5 text-muted-foreground">
              {cadenceLabel("0 9 * * *", t, i18n.language)}
            </td>
            <td className="px-2 py-1.5">
              <span className={`${PILL_CLASS} bg-success/10 text-success`}>
                {t("strategySchedules.statusActive")}
              </span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}

export function NextRunPreview() {
  const { t } = useTranslation();
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-md border bg-card px-2.5 py-1.5 text-xs">
      <span className="font-medium">{t("strategyLibrary.demoStrategyName")}</span>
      <RunBadge status="completed" />
      <span className="font-mono text-[11px] text-muted-foreground">
        {t("strategyLibrary.inspectorVersion", { version: "1.0.0" })}
      </span>
    </div>
  );
}

export function FramePreview({ index }: { index: number }) {
  switch (index) {
    case 0:
      return <SolidifyPreview />;
    case 1:
      return <GapsPreview />;
    case 2:
      return <ReviewPreview />;
    case 3:
      return <SchedulePreview />;
    case 4:
      return <NextRunPreview />;
    default:
      return null;
  }
}

export function FinalLink() {
  const { t } = useTranslation();
  return (
    <Link
      to="/strategies?demo=1"
      data-testid="demo-final-link"
      className="inline-flex w-fit rounded-md bg-primary px-2.5 py-1 text-xs font-medium text-primary-foreground transition hover:opacity-90"
    >
      {t("strategyLibrary.demoFinalLink")}
    </Link>
  );
}

export function FrameContent({ index, staticFrame }: { index: number; staticFrame: boolean }) {
  const { t } = useTranslation();
  const frame = DEMO_FRAMES[index];
  return (
    <article
      data-testid={staticFrame ? "demo-static-frame" : "demo-frame"}
      data-frame-id={frame.id}
      className="flex flex-col gap-2 rounded-md border bg-background/60 p-2.5"
    >
      <h3 className="text-xs font-semibold">{t(frame.titleKey)}</h3>
      <p className="text-[11px] text-muted-foreground">{t(frame.bodyKey)}</p>
      <FramePreview index={index} />
      {index === LAST_INDEX && <FinalLink />}
    </article>
  );
}