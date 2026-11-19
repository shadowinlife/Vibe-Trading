import { act, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import i18n from "@/i18n";
import {
  DEMO_SEEN_STORAGE_KEY,
  DemoStoryboard,
} from "@/components/strategy/DemoStoryboard";
import { DEMO_FRAMES, DEMO_TOTAL_MS } from "@/components/strategy/demoScript";

beforeAll(async () => {
  await i18n.changeLanguage("en");
});

beforeEach(() => {
  window.localStorage.clear();
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

function mockReducedMotion(matches: boolean) {
  vi.spyOn(window, "matchMedia").mockReturnValue({
    matches,
    media: "(prefers-reduced-motion: reduce)",
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  } as MediaQueryList);
}

function renderStoryboard(openRequest = 0) {
  return render(
    <MemoryRouter>
      <DemoStoryboard openRequest={openRequest} />
    </MemoryRouter>,
  );
}

describe("DemoStoryboard", () => {
  it("opens on the first visit and advances through all six frames", () => {
    expect(DEMO_FRAMES).toHaveLength(6);
    // The required per-frame durations (10/15/15/15/20/10) sum to 85s and
    // stay inside the planned 60-90s window.
    expect(DEMO_TOTAL_MS).toBe(85_000);
    expect(DEMO_FRAMES.map((frame) => frame.durationMs)).toEqual([
      10_000, 15_000, 15_000, 15_000, 20_000, 10_000,
    ]);

    renderStoryboard();
    expect(screen.getByTestId("demo-storyboard")).toBeInTheDocument();

    DEMO_FRAMES.forEach((frame, index) => {
      expect(screen.getByTestId("demo-frame")).toHaveAttribute(
        "data-frame-id",
        frame.id,
      );
      if (index < DEMO_FRAMES.length - 1) {
        act(() => {
          vi.advanceTimersByTime(frame.durationMs);
        });
      }
    });

    expect(screen.getByTestId("demo-frame")).toHaveAttribute(
      "data-frame-id",
      "boundary",
    );
    // Frame six stops the timer: playback has ended, not merely paused.
    expect(screen.getByTestId("demo-playpause")).toHaveTextContent("Play");
    act(() => {
      vi.advanceTimersByTime(DEMO_TOTAL_MS);
    });
    expect(screen.getByTestId("demo-frame")).toHaveAttribute(
      "data-frame-id",
      "boundary",
    );
  });

  it("renders a static storyboard without any interval under reduced motion", () => {
    const intervalSpy = vi.spyOn(window, "setInterval");
    mockReducedMotion(true);

    renderStoryboard();

    const frames = screen.getAllByTestId("demo-static-frame");
    expect(frames).toHaveLength(6);
    expect(frames.map((frame) => frame.getAttribute("data-frame-id"))).toEqual(
      DEMO_FRAMES.map((frame) => frame.id),
    );
    expect(screen.getByTestId("demo-static")).toBeInTheDocument();
    expect(screen.queryByTestId("demo-playpause")).not.toBeInTheDocument();
    expect(intervalSpy).not.toHaveBeenCalled();

    act(() => {
      vi.advanceTimersByTime(120_000);
    });

    expect(screen.getAllByTestId("demo-static-frame")).toHaveLength(6);
    expect(screen.getAllByTestId("demo-final-link")).toHaveLength(1);
    expect(intervalSpy).not.toHaveBeenCalled();
  });

  it("does not auto-open on a later visit but can be opened on demand", () => {
    window.localStorage.setItem(DEMO_SEEN_STORAGE_KEY, "1");

    const { rerender } = renderStoryboard();
    expect(screen.queryByTestId("demo-storyboard")).not.toBeInTheDocument();

    rerender(
      <MemoryRouter>
        <DemoStoryboard openRequest={1} />
      </MemoryRouter>,
    );
    expect(screen.getByTestId("demo-storyboard")).toBeInTheDocument();
  });

  it("ends on one navigation link and never calls fetch", () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);

    renderStoryboard();
    fireEvent.click(screen.getByTestId("demo-skip"));

    expect(screen.queryByTestId("demo-frame")).toHaveAttribute(
      "data-frame-id",
      "boundary",
    );
    const links = screen.getAllByTestId("demo-final-link");
    expect(links).toHaveLength(1);
    expect(links[0].tagName).toBe("A");
    expect(links[0]).toHaveAttribute("href", "/strategies?demo=1");

    fireEvent.click(links[0]);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("closes on Escape and stops every timer", () => {
    renderStoryboard();
    expect(screen.getByTestId("demo-storyboard")).toBeInTheDocument();

    fireEvent.keyDown(window, { key: "Escape" });

    expect(screen.queryByTestId("demo-storyboard")).not.toBeInTheDocument();
    act(() => {
      vi.advanceTimersByTime(DEMO_TOTAL_MS * 2);
    });
    expect(screen.queryByTestId("demo-storyboard")).not.toBeInTheDocument();
  });
});