import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import i18n from "@/i18n";
import { api } from "@/lib/api";
import { NAVIGATION_GROUPS } from "../navigation";
import { SidebarNavigation } from "../SidebarNavigation";
import { StrategyEntry } from "../StrategyEntry";
import { PageHelp } from "../PageHelp";

vi.mock("@/lib/api", () => ({
  api: { getStrategySummary: vi.fn() },
}));

const SUMMARY = {
  active_count: 3,
  runs_today: 1,
  failed_today: 2,
  next_run_at: Date.UTC(2026, 9, 26, 1, 30),
  success_rate_7d: 0.5,
};

beforeAll(async () => { await i18n.changeLanguage("en"); });

beforeEach(() => {
  // Sync tests never await the summary, so keep it pending to avoid state
  // updates after the assertions have already run.
  vi.mocked(api.getStrategySummary).mockReset().mockReturnValue(new Promise(() => {}));
});

function renderNavigation(path = "/", collapsed = false) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <SidebarNavigation
        collapsed={collapsed}
        strategyEntry={(compact, drawer) => <StrategyEntry collapsed={compact} inDrawer={drawer} />}
      />
      <PageHelp />
    </MemoryRouter>,
  );
}

it("keeps every feature directly reachable and distinguishes tasks from tools and setup", () => {
  renderNavigation();
  expect(within(screen.getByRole("region", { name: "Everyday research" })).getAllByRole("link").map((link) => link.getAttribute("href")))
    .toEqual(["/", "/reports", "/paper-trading", "/portfolio"]);
  expect(screen.getByRole("link", { name: "Strategy library" })).toHaveAttribute("href", "/strategies");
  expect(screen.getByRole("link", { name: "Strategy schedules" })).toHaveAttribute("href", "/strategy-schedules");
  expect(within(screen.getByRole("region", { name: "Specialized analysis" })).getAllByRole("link").map((link) => link.getAttribute("href")))
    .toEqual(["/alpha-zoo", "/options", "/correlation"]);
  expect(within(screen.getByRole("region", { name: "Automation & setup" })).getAllByRole("link").map((link) => link.getAttribute("href")))
    .toEqual(["/pixel-office", "/scheduled", "/runtime", "/settings"]);
  expect(screen.getByRole("link", { name: "Research assistant" })).toHaveAttribute("aria-current", "page");
});

it("orders the strategy entry between research and analysis", () => {
  renderNavigation();
  expect(NAVIGATION_GROUPS.map((group) => group.id)).toEqual([
    "research",
    "strategy",
    "analysis",
    "automation",
  ]);
  const strategyTitle = screen.getByText("Strategy");
  const research = screen.getByRole("region", { name: "Everyday research" });
  const analysis = screen.getByRole("region", { name: "Specialized analysis" });
  expect(research.compareDocumentPosition(strategyTitle) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(strategyTitle.compareDocumentPosition(analysis) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
});

it("marks the active strategy entry and leaves the sibling inactive", () => {
  renderNavigation("/strategies");
  expect(screen.getByRole("link", { name: "Strategy library" })).toHaveAttribute("aria-current", "page");
  expect(screen.getByRole("link", { name: "Strategy schedules" })).not.toHaveAttribute("aria-current");
});

it("marks the schedules entry active on the schedules route", () => {
  renderNavigation("/strategy-schedules");
  expect(screen.getByRole("link", { name: "Strategy schedules" })).toHaveAttribute("aria-current", "page");
  expect(screen.getByRole("link", { name: "Strategy library" })).not.toHaveAttribute("aria-current");
});

it("shows the library summary from the API beneath the two entries", async () => {
  vi.mocked(api.getStrategySummary).mockResolvedValue(SUMMARY);
  renderNavigation();

  expect(await screen.findByText("3 active")).toBeInTheDocument();
  expect(screen.getByText(/^Next /)).toBeInTheDocument();
  expect(screen.getByText("2 failed")).toBeInTheDocument();
  expect(api.getStrategySummary).toHaveBeenCalledTimes(1);
});

it("keeps both entries usable and shows no counts when the summary request fails", async () => {
  vi.mocked(api.getStrategySummary).mockRejectedValue(new Error("summary unavailable"));
  renderNavigation("/strategies");

  await waitFor(() => expect(api.getStrategySummary).toHaveBeenCalledTimes(1));
  await act(async () => {});

  expect(screen.queryByText(/\d+ active/)).not.toBeInTheDocument();
  expect(screen.queryByText(/\d+ failed/)).not.toBeInTheDocument();
  expect(screen.queryByText(/^Next /)).not.toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Strategy library" })).toHaveAttribute("href", "/strategies");
  expect(screen.getByRole("link", { name: "Strategy schedules" })).toHaveAttribute("href", "/strategy-schedules");
});

it("explains the strategy library through the page help keys", () => {
  renderNavigation("/strategies");
  fireEvent.click(screen.getByText("How to use Strategy library"));
  expect(screen.getByText(/replayable specifications/)).toBeVisible();
  expect(screen.getByText(/one confirmation step/)).toBeVisible();
});

it("explains strategy schedules through the page help keys", () => {
  renderNavigation("/strategy-schedules");
  fireEvent.click(screen.getByText("How to use Strategy schedules"));
  expect(screen.getByText(/how its recent runs ended/)).toBeVisible();
});

it("retains labels and help in a compact sidebar, including nested factor routes", () => {
  renderNavigation("/alpha-zoo/compare", true);
  const link = screen.getByRole("link", { name: "Factor research" });
  expect(link).toHaveAttribute("aria-current", "page");
  expect(link).toHaveAttribute("title", expect.stringContaining("stock-selection signals"));
  expect(screen.getByRole("navigation").querySelectorAll('[aria-current="page"]')).toHaveLength(1);
});

it("explains the backtest prerequisite and the separate location of chat-generated documents", () => {
  renderNavigation("/reports");
  fireEvent.click(screen.getByText("How to use Backtest reports"));
  expect(screen.getByText(/Download PDFs and other chat-generated documents/)).toBeVisible();
  expect(screen.getByRole("link", { name: "Start with the research assistant" })).toHaveAttribute("href", "/");
});

it("resets page help on navigation and states the scheduler prerequisite", () => {
  renderNavigation("/reports");
  fireEvent.click(screen.getByText("How to use Backtest reports"));
  fireEvent.click(screen.getByRole("link", { name: "Scheduled research" }));
  const summary = screen.getByText("How to use Scheduled research");
  expect(summary.closest("details")).not.toHaveAttribute("open");
  fireEvent.click(summary);
  expect(screen.getByText(/local scheduler must be enabled/)).toBeVisible();
});

it("opens a labeled feature menu in compact mode and returns focus on Escape", () => {
  renderNavigation("/", true);
  const trigger = screen.getByRole("button", { name: "Browse features" });
  fireEvent.click(trigger);
  const menu = screen.getByRole("navigation", { name: "Browse features" });
  expect(within(menu).getByText("Options analysis")).toBeVisible();
  expect(within(menu).getByText("Pixel Office")).toBeVisible();
  expect(within(menu).getByText("Trading status")).toBeVisible();
  expect(within(menu).getAllByRole("link")).toHaveLength(13);
  fireEvent.keyDown(document, { key: "Escape" });
  expect(screen.queryByRole("navigation", { name: "Browse features" })).not.toBeInTheDocument();
  expect(trigger).toHaveFocus();
});

it("carries both strategy entries into the compact feature drawer", () => {
  renderNavigation("/", true);
  fireEvent.click(screen.getByRole("button", { name: "Browse features" }));
  const menu = screen.getByRole("navigation", { name: "Browse features" });
  const library = within(menu).getByRole("link", { name: "Strategy library" });
  expect(library).toHaveAttribute("href", "/strategies");
  expect(library.querySelector("span")).not.toHaveClass("max-md:hidden");
  expect(within(menu).getByRole("link", { name: "Strategy schedules" })).toHaveAttribute("href", "/strategy-schedules");
});

it("collapses the strategy entry to icons on the narrow sidebar rail", () => {
  renderNavigation();
  const library = screen.getByRole("link", { name: "Strategy library" });
  expect(library).toHaveClass("max-md:justify-center");
  expect(library.querySelector("span")).toHaveClass("max-md:hidden");
});

it("closes the feature menu after selecting a destination", () => {
  renderNavigation("/", true);
  fireEvent.click(screen.getByRole("button", { name: "Browse features" }));
  const menu = screen.getByRole("navigation", { name: "Browse features" });
  fireEvent.click(within(menu).getByRole("link", { name: "Backtest reports" }));
  expect(screen.queryByRole("navigation", { name: "Browse features" })).not.toBeInTheDocument();
  expect(screen.getByText("How to use Backtest reports")).toBeInTheDocument();
});


it("links paper accounts and explains completed-session simulation", () => {
  renderNavigation("/paper-trading");
  expect(screen.getByRole("link", { name: "Paper trading" })).toHaveAttribute("aria-current", "page");
  fireEvent.click(screen.getByText("How to use Paper trading"));
  expect(screen.getByText(/No broker orders are sent/)).toBeVisible();
  expect(screen.getByText(/Only completed daily bars can settle/)).toBeVisible();
});