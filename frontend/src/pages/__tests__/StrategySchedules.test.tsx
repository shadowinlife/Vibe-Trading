import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import i18n from "@/i18n";
import { StrategySchedules } from "@/pages/StrategySchedules";
import {
  api,
  type StrategySchedule,
  type StrategySummary,
} from "@/lib/api";

const apiMock = vi.hoisted(() => ({
  listStrategySchedules: vi.fn(),
  listStrategies: vi.fn(),
  createStrategySchedule: vi.fn(),
  commitStrategySchedule: vi.fn(),
  deleteStrategySchedule: vi.fn(),
  getStrategyScheduleRuns: vi.fn(),
  runStrategyScheduleNow: vi.fn(),
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, api: apiMock };
});

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

const NOW = 1_800_000_000_000;

function strategy(
  id: string,
  name: string,
  kind: StrategySummary["kind"],
  readiness: StrategySummary["readiness"],
): StrategySummary {
  return {
    id,
    name,
    kind,
    readiness,
    version: "1.0.0",
    universe: "csi300",
    demo: false,
    evidence: {
      has_bench: true,
      last_verified: new Date(NOW - 60_000).toISOString(),
      quality: "high",
    },
    schedule: { count: 0, next_run_at: null },
    last_run: null,
  };
}

const STRATEGIES: StrategySummary[] = [
  strategy("stg_code", "Alpha Code", "code", "ready"),
  strategy("stg_wf", "Beta Workflow", "workflow", "ready"),
  strategy("stg_draft", "Draft One", "workflow", "draft"),
];

function run(
  runId: string,
  status: "completed" | "failed" | "skipped" | "missed",
  startedAt: number,
) {
  return {
    run_id: runId,
    job_id: "job_runs",
    strategy_id: "stg_code",
    strategy_version: "1.0.0",
    status,
    started_at: startedAt,
    finished_at: startedAt + 90_000,
    session_id: null,
    run_dir: null,
    summary: "",
    error: "",
    error_kind: "",
  };
}

const SCHEDULE: StrategySchedule = {
  job_id: "job_runs",
  strategy_id: "stg_code",
  strategy_version: "1.0.0",
  schedule: "0 9 * * 1-5",
  timezone: "Asia/Shanghai",
  status: "pending",
  next_run_at: NOW + 3_600_000,
  last_runs: [
    run("run-completed", "completed", NOW - 60_000),
    run("run-failed", "failed", NOW - 120_000),
    run("run-skipped", "skipped", NOW - 180_000),
    run("run-missed", "missed", NOW - 240_000),
    run("run-fifth", "completed", NOW - 300_000),
  ],
};

const CODE_RESULT = {
  requires_confirmation: false as const,
  job_id: "job_created",
  proposal_id: null,
  confirm_token: null,
  strategy_version: "1.0.0",
};

const WORKFLOW_RESULT = {
  requires_confirmation: true as const,
  job_id: null,
  proposal_id: "ssp_9",
  confirm_token: "tok_one_time",
  strategy_version: "1.0.0",
};

beforeAll(async () => {
  await i18n.changeLanguage("en");
});

beforeEach(() => {
  vi.clearAllMocks();
  apiMock.listStrategySchedules.mockResolvedValue([SCHEDULE]);
  apiMock.listStrategies.mockResolvedValue({
    items: STRATEGIES,
    total: STRATEGIES.length,
    limit: 200,
    offset: 0,
  });
  apiMock.createStrategySchedule.mockResolvedValue(CODE_RESULT);
  apiMock.commitStrategySchedule.mockResolvedValue(SCHEDULE);
  apiMock.deleteStrategySchedule.mockResolvedValue(undefined);
  apiMock.getStrategyScheduleRuns.mockResolvedValue(SCHEDULE.last_runs);
  apiMock.runStrategyScheduleNow.mockResolvedValue(SCHEDULE);
});

function renderPage() {
  return render(
    <MemoryRouter>
      <StrategySchedules />
    </MemoryRouter>,
  );
}

async function openModal() {
  fireEvent.click(await screen.findByRole("button", { name: "New schedule" }));
  return screen.findByTestId("create-schedule-dialog");
}

async function reachStep3(dialog: HTMLElement, strategyName: RegExp) {
  const next = within(dialog).getByRole("button", { name: "Next" });
  expect(next).toBeDisabled();
  fireEvent.click(within(dialog).getByLabelText(strategyName));
  expect(next).toBeEnabled();
  fireEvent.click(next);
  expect(within(dialog).getByTestId("schedule-step-2")).toBeInTheDocument();
  expect(within(dialog).queryByRole("button", { name: "Create schedule" })).toBeNull();
  fireEvent.click(within(dialog).getByRole("button", { name: "Next" }));
  expect(within(dialog).getByTestId("schedule-step-3")).toBeInTheDocument();
}

describe("StrategySchedules", () => {
  it("gates the next step until a ready strategy is chosen", async () => {
    renderPage();
    const dialog = await openModal();

    expect(within(dialog).getByText("Only ready strategies can be scheduled.")).toBeInTheDocument();
    expect(within(dialog).queryByText("Draft One")).toBeNull();
    expect(
      within(dialog).getByText("1 strategies are not ready and are hidden."),
    ).toBeInTheDocument();

    await reachStep3(dialog, /Alpha Code/);
    expect(
      within(dialog).getByRole("button", { name: "Create schedule" }),
    ).toBeInTheDocument();
  });

  it("creates a code schedule directly with no confirmation card", async () => {
    renderPage();
    const dialog = await openModal();
    await reachStep3(dialog, /Alpha Code/);

    fireEvent.click(within(dialog).getByRole("button", { name: "Create schedule" }));

    await waitFor(() => {
      expect(apiMock.createStrategySchedule).toHaveBeenCalledTimes(1);
    });
    expect(apiMock.createStrategySchedule).toHaveBeenCalledWith(
      "stg_code",
      expect.objectContaining({
schedule: "0 9 * * *",
        timezone: expect.any(String),
        run_timeout_s: 3600,
      }),
    );
    expect(screen.queryByTestId("schedule-confirm-card")).toBeNull();
    expect(apiMock.commitStrategySchedule).not.toHaveBeenCalled();
    await waitFor(() => {
      expect(screen.queryByTestId("create-schedule-dialog")).toBeNull();
    });
  });

  it("stages a workflow proposal and commits it exactly once", async () => {
    apiMock.createStrategySchedule.mockResolvedValue(WORKFLOW_RESULT);
    renderPage();
    const dialog = await openModal();
    await reachStep3(dialog, /Beta Workflow/);

    fireEvent.click(within(dialog).getByRole("button", { name: "Create schedule" }));

    const card = await within(dialog).findByTestId("schedule-confirm-card");
    expect(within(card).getByText("ssp_9")).toBeInTheDocument();
    expect(apiMock.commitStrategySchedule).not.toHaveBeenCalled();

    fireEvent.click(within(card).getByRole("button", { name: "Confirm & create" }));

    await waitFor(() => {
      expect(apiMock.commitStrategySchedule).toHaveBeenCalledTimes(1);
    });
    expect(apiMock.commitStrategySchedule).toHaveBeenCalledWith("ssp_9", "tok_one_time");
    await waitFor(() => {
      expect(screen.queryByTestId("create-schedule-dialog")).toBeNull();
    });
  });

  it("leaves commit uncalled when the confirmation card is closed", async () => {
    apiMock.createStrategySchedule.mockResolvedValue(WORKFLOW_RESULT);
    renderPage();
    const dialog = await openModal();
    await reachStep3(dialog, /Beta Workflow/);

    fireEvent.click(within(dialog).getByRole("button", { name: "Create schedule" }));
    const card = await within(dialog).findByTestId("schedule-confirm-card");

    fireEvent.click(within(card).getByRole("button", { name: "Cancel" }));

    await waitFor(() => {
      expect(screen.queryByTestId("create-schedule-dialog")).toBeNull();
    });
    expect(apiMock.commitStrategySchedule).not.toHaveBeenCalled();
  });

  it("renders four distinct last-run states and never folds them into failed", async () => {
    renderPage();
    const row = await screen.findByTestId("schedule-row-job_runs");

    expect(within(row).getAllByText("Completed")).toHaveLength(2);
    expect(within(row).getAllByText("Failed")).toHaveLength(1);
    expect(within(row).getAllByText("Skipped")).toHaveLength(1);
    expect(within(row).getAllByText("Missed")).toHaveLength(1);
  });

  it("expands a row to the five newest run details", async () => {
    renderPage();
    const row = await screen.findByTestId("schedule-row-job_runs");

    fireEvent.click(
      within(row).getByRole("button", { name: "Show recent runs for Alpha Code" }),
    );

    await waitFor(() => {
      expect(screen.getAllByTestId(/^schedule-run-detail-/)).toHaveLength(5);
    });
    expect(screen.getByText("Run run-completed")).toBeInTheDocument();
  });
});