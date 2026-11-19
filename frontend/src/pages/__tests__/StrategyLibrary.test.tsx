import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import i18n from "@/i18n";
import { StrategyLibrary } from "@/pages/StrategyLibrary";
import {
  ApiError,
  api,
  type StrategyDetail,
  type StrategyLibrarySummary,
  type StrategyListResponse,
  type StrategyReadiness,
  type StrategySummary,
} from "@/lib/api";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      listStrategies: vi.fn(),
      getStrategySummary: vi.fn(),
      seedDemoStrategy: vi.fn(),
      getStrategy: vi.fn(),
    },
  };
});

const mocked = api as unknown as {
  listStrategies: ReturnType<typeof vi.fn>;
  getStrategySummary: ReturnType<typeof vi.fn>;
  seedDemoStrategy: ReturnType<typeof vi.fn>;
  getStrategy: ReturnType<typeof vi.fn>;
};

const READINESS_ORDER: StrategyReadiness[] = [
  "ready",
  "ready",
  "ready",
  "ready",
  "ready",
  "ready",
  "ready",
  "ready",
  "draft",
  "needs_work",
  "reviewing",
  "rejected",
];

function makeStrategy(index: number, overrides: Partial<StrategySummary> = {}): StrategySummary {
  const now = Date.now();
  return {
    id: `strategy-${index}`,
    name: `Strategy ${index}`,
    kind: index % 2 === 0 ? "workflow" : "code",
    readiness: "ready",
    version: "1.0.0",
    universe: "csi300",
    demo: false,
    evidence: {
      has_bench: true,
      last_verified: new Date(now - 60_000).toISOString(),
      quality: "high",
    },
    schedule: { count: index % 3, next_run_at: now + 3_600_000 },
    last_run: {
      status: "completed",
      finished_at: now - 120_000,
      run_id: `run-${index}`,
    },
    ...overrides,
  };
}

function makeItems(): StrategySummary[] {
  return READINESS_ORDER.map((readiness, offset) =>
    makeStrategy(offset + 1, { readiness, demo: offset === 0 }),
  );
}

const ITEMS = makeItems();
const LIST: StrategyListResponse = { items: ITEMS, total: 12, limit: 50, offset: 0 };
const SUMMARY: StrategyLibrarySummary = {
  active_count: 3,
  runs_today: 1,
  failed_today: 0,
  next_run_at: null,
  success_rate_7d: 0.75,
};
const DETAIL: StrategyDetail = {
  ...ITEMS[0],
  spec: {},
  evaluator_verdict: null,
  previous_verdict: null,
  gaps: [],
  bench_history: [],
  schedules: [],
  run_history: [],
  config_summary: { keys: 0, source: null, universe: "csi300" },
  signal_engine_path: "",
};

beforeAll(async () => {
  await i18n.changeLanguage("en");
});

beforeEach(() => {
  vi.clearAllMocks();
  mocked.listStrategies.mockResolvedValue(LIST);
  mocked.getStrategySummary.mockResolvedValue(SUMMARY);
  mocked.getStrategy.mockResolvedValue(DETAIL);
  mocked.seedDemoStrategy.mockResolvedValue({
    created: true,
    strategy_id: "strategy-1",
    version: "1.0.0",
    job_id: "job-1",
    readiness: "ready",
  });
});

function renderPage(path = "/strategies") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <StrategyLibrary />
    </MemoryRouter>,
  );
}

describe("StrategyLibrary", () => {
  it("renders all twelve rows with the KPI bar and a demo badge", async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getAllByTestId(/^strategy-row-/)).toHaveLength(12);
    });
    expect(screen.getByText("Total")).toBeInTheDocument();
    expect(screen.getByText("Schedulable")).toBeInTheDocument();
    expect(screen.getByText("Active schedules")).toBeInTheDocument();
    expect(screen.getByText("75%")).toBeInTheDocument();
    expect(screen.getByText("Demo")).toBeInTheDocument();
  });

  it("filters to the eight schedulable strategies", async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getAllByTestId(/^strategy-row-/)).toHaveLength(12);
    });

    fireEvent.change(screen.getByLabelText("Readiness"), { target: { value: "ready" } });

    await waitFor(() => {
      expect(screen.getAllByTestId(/^strategy-row-/)).toHaveLength(8);
    });
    expect(screen.queryByTestId("strategy-row-strategy-12")).not.toBeInTheDocument();
  });

  it("shows a three-step empty state with a load-demo button", async () => {
    mocked.listStrategies.mockResolvedValue({ items: [], total: 0, limit: 50, offset: 0 });
    renderPage();

    expect(
      await screen.findByRole("button", { name: "Load demo strategy" }),
    ).toBeInTheDocument();
    expect(screen.getByText("No strategies yet")).toBeInTheDocument();
    expect(screen.getByText(/Save a backtest result as a strategy/)).toBeInTheDocument();
    expect(screen.getByText(/Run the reviewer and close every gap/)).toBeInTheDocument();
    expect(screen.getByText(/Schedule it once it is ready/)).toBeInTheDocument();
  });

  it("recovers from a 500 after an inline retry", async () => {
    mocked.listStrategies
      .mockRejectedValueOnce(new ApiError("Server exploded", 500))
      .mockResolvedValueOnce(LIST);
    renderPage();

    expect(await screen.findByText("Server exploded")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));

    await waitFor(() => {
      expect(screen.getAllByTestId(/^strategy-row-/)).toHaveLength(12);
    });
    expect(screen.queryByText("Server exploded")).not.toBeInTheDocument();
  });

  it("auto-opens the demo inspector for ?demo=1", async () => {
    renderPage("/strategies?demo=1");

    const dialog = await screen.findByRole("dialog");
    await waitFor(() => {
      expect(within(dialog).getByText("Strategy 1")).toBeInTheDocument();
    });
  });
});