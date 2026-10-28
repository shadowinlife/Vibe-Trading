import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useState } from "react";
import { MemoryRouter } from "react-router";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import i18n from "@/i18n";
import { StrategyInspector } from "@/components/strategy/StrategyInspector";
import { api, type StrategyDetail, type StrategyGap } from "@/lib/api";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getStrategy: vi.fn(),
    },
  };
});

const mocked = api as unknown as {
  getStrategy: ReturnType<typeof vi.fn>;
};

const GAPS: StrategyGap[] = [
  {
    code: "missing_preconditions",
    field: "preconditions",
    message: "preconditions are empty",
    fix_hint: "list the conditions to check before the run",
  },
  {
    code: "tool_not_allowed",
    field: "steps[2].tool",
    message: "tool is not allowlisted",
    fix_hint: "replace it with an allowlisted research tool",
  },
];

function makeDetail(overrides: Partial<StrategyDetail> = {}): StrategyDetail {
  const now = Date.now();
  return {
    id: "strategy-1",
    name: "Momentum Alpha",
    kind: "code",
    readiness: "ready",
    version: "1.2.0",
    universe: "csi300",
    demo: false,
    evidence: {
      has_bench: true,
      last_verified: new Date(now - 90 * 60_000).toISOString(),
      quality: "high",
    },
    schedule: { count: 2, next_run_at: now + 3_600_000 },
    last_run: { status: "completed", finished_at: now - 120_000, run_id: "run-1" },
    spec: {
      name: "Momentum Alpha",
      kind: "code",
      thesis: "Buy the strongest names with volume confirmation.",
      universe: "csi300",
      inputs: [
        { name: "prices", source: "backtest", required: true, missing_policy: "stop" },
      ],
      preconditions: ["the exchange is open"],
      steps: [],
      output_contract: null,
      failure_handling: "Stop and report the failing step.",
    },
    evaluator_verdict: {
      verdict: "pass",
      must_pass: [
        { id: "goal_decidable", pass: true, why: "thesis is testable" },
        { id: "inputs_complete", pass: true },
        { id: "preconditions_checkable", pass: true },
        { id: "steps_actionable", pass: true },
        { id: "tools_allowlisted", pass: true },
        { id: "output_contract_parsable", pass: true },
        { id: "failure_handling_explicit", pass: true },
        { id: "context_self_contained", pass: true },
      ],
      summary: "All eight checks pass.",
      created_at: new Date(now - 90 * 60_000).toISOString(),
    },
    previous_verdict: {
      verdict: "fail",
      artifact_version: "0.9.0",
      summary: "Earlier version failed.",
    },
    gaps: [],
    bench_history: [
      {
        id: 1,
        bench_type: "backtest",
        sharpe: 1.42,
        annual_return: 0.18,
        max_drawdown: -0.09,
        calmar: 2.0,
        category: "momentum",
        run_dir: "/runs/abc",
        created_at: new Date(now - 90 * 60_000).toISOString(),
      },
    ],
    schedules: [
      {
        job_id: "job-1",
        strategy_id: "strategy-1",
        strategy_version: "1.2.0",
        schedule: "0 9 * * 1",
        timezone: "Asia/Shanghai",
        status: "active",
        next_run_at: now + 3_600_000,
        last_runs: [
          {
            run_id: "sched-run-1",
            job_id: "job-1",
            strategy_id: "strategy-1",
            strategy_version: "1.2.0",
            status: "completed",
            started_at: now - 300_000,
            finished_at: now - 240_000,
            session_id: null,
            run_dir: null,
            summary: "",
            error: "",
            error_kind: "",
          },
        ],
      },
    ],
    run_history: [
      {
        run_id: "run-2",
        job_id: "job-1",
        strategy_id: "strategy-1",
        strategy_version: "1.2.0",
        status: "failed",
        started_at: now - 600_000,
        finished_at: now - 540_000,
        session_id: null,
        run_dir: null,
        summary: "",
        error: "market data bridge unavailable",
        error_kind: "data_unavailable",
      },
    ],
    config_summary: { keys: 4, source: "local", universe: "csi300" },
    signal_engine_path: "/srv/strategies/strategy-1/code/signal_engine.py",
    ...overrides,
  };
}

function Harness({ detail }: { detail: StrategyDetail }) {
  const [open, setOpen] = useState(false);
  return (
    <MemoryRouter>
      <button type="button" onClick={() => setOpen(true)}>
        open inspector
      </button>
      {open && (
        <StrategyInspector strategyId={detail.id} onClose={() => setOpen(false)} />
      )}
    </MemoryRouter>
  );
}

async function openInspector(detail: StrategyDetail) {
  mocked.getStrategy.mockResolvedValue(detail);
  render(<Harness detail={detail} />);
  const trigger = screen.getByRole("button", { name: "open inspector" });
  trigger.focus();
  fireEvent.click(trigger);
  const dialog = await screen.findByRole("dialog");
  await within(dialog).findByText(detail.name);
  return { trigger, dialog };
}

beforeAll(async () => {
  await i18n.changeLanguage("en");
});

beforeEach(() => {
  vi.clearAllMocks();
});

describe("StrategyInspector", () => {
  it("switches through all six tabs", async () => {
    const { dialog } = await openInspector(makeDetail());

    expect(within(dialog).getByText("What it does")).toBeInTheDocument();
    expect(
      within(dialog).getByText("Buy the strongest names with volume confirmation."),
    ).toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole("tab", { name: "Contract" }));
    expect(within(dialog).getByText("Inputs")).toBeInTheDocument();
    expect(within(dialog).getByText("Config summary")).toBeInTheDocument();
    expect(
      within(dialog).getByText("/srv/strategies/strategy-1/code/signal_engine.py"),
    ).toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole("tab", { name: "Review" }));
    expect(within(dialog).getByText("Must-pass checks")).toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole("tab", { name: "Evidence" }));
    expect(within(dialog).getByText("Bench history")).toBeInTheDocument();
    expect(within(dialog).getByText("Most recent backtest")).toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole("tab", { name: "Schedule" }));
    expect(within(dialog).getByText("Recent scheduled runs")).toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole("tab", { name: "Runs" }));
    expect(within(dialog).getByText("Recent runs")).toBeInTheDocument();
    expect(
      within(dialog).getByText("Error: market data bridge unavailable"),
    ).toBeInTheDocument();

    expect(within(dialog).getByRole("tab", { name: "Runs" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });

  it("closes on Escape and returns focus to the trigger", async () => {
    const { trigger } = await openInspector(makeDetail());

    expect(
      screen.getByRole("button", { name: "Close strategy inspector" }),
    ).toHaveFocus();

    fireEvent.keyDown(document, { key: "Escape" });

    await waitFor(() => {
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });
    expect(trigger).toHaveFocus();
  });

  it("disables the primary action with a reason when the strategy is not ready", async () => {
    const { dialog } = await openInspector(
      makeDetail({ readiness: "needs_work", gaps: GAPS }),
    );

    const action = within(dialog).getByRole("button", { name: "Enable schedule" });
    expect(action).toBeDisabled();
    expect(action).toHaveAttribute("title", "2 gaps remain.");
  });

  it("enables scheduling for a ready strategy without saving anything", async () => {
    const { dialog } = await openInspector(makeDetail());

    const action = within(dialog).getByRole("button", { name: "Enable schedule" });
    expect(action).toBeEnabled();
    expect(action).not.toHaveAttribute("title");
  });

  it("renders every gap as its own row with code, message and fix hint", async () => {
    const { dialog } = await openInspector(
      makeDetail({ readiness: "needs_work", gaps: GAPS }),
    );

    fireEvent.click(within(dialog).getByRole("tab", { name: "Review" }));

    const rows = within(dialog).getAllByTestId("inspector-gap");
    expect(rows).toHaveLength(2);
    expect(within(rows[0]).getByText("missing_preconditions")).toBeInTheDocument();
    expect(within(rows[0]).getByText("preconditions are empty")).toBeInTheDocument();
    expect(
      within(rows[0]).getByText(
        "Fix: list the conditions to check before the run",
      ),
    ).toBeInTheDocument();
    expect(within(rows[1]).getByText("tool_not_allowed")).toBeInTheDocument();
    expect(within(rows[1]).getByText("steps[2].tool")).toBeInTheDocument();
  });

  it("shows the current verdict and the previous version's verdict separately", async () => {
    const { dialog } = await openInspector(makeDetail());

    fireEvent.click(within(dialog).getByRole("tab", { name: "Review" }));

    expect(within(dialog).getByText("Current verdict")).toBeInTheDocument();
    expect(within(dialog).getByText("Previous version's verdict")).toBeInTheDocument();
    expect(
      within(dialog).getByText("Only the immediately preceding verdict is kept."),
    ).toBeInTheDocument();
    expect(within(dialog).getByText("Fail")).toBeInTheDocument();
    expect(within(dialog).getByText("v0.9.0")).toBeInTheDocument();
    expect(
      within(dialog).getByTestId("inspector-check-goal_decidable"),
    ).toHaveTextContent("Pass");
  });
});