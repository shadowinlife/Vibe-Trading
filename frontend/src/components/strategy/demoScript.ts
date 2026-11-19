export interface DemoFrame {
  readonly id: string;
  readonly durationMs: number;
  readonly titleKey: string;
  readonly bodyKey: string;
}

/**
 * Six frames of the strategy-library storyboard.
 *
 * Durations intentionally add up to 90 seconds: the walkthrough is a
 * self-contained demo, not a replacement for the real pages. The final
 * frame points at the library instead of seeding anything, so viewing the
 * demo never mutates the user's data.
 */
export const DEMO_FRAMES = [
  {
    id: "solidify",
    durationMs: 10_000,
    titleKey: "strategyLibrary.demoFrame1Title",
    bodyKey: "strategyLibrary.demoFrame1Body",
  },
  {
    id: "gaps",
    durationMs: 15_000,
    titleKey: "strategyLibrary.demoFrame2Title",
    bodyKey: "strategyLibrary.demoFrame2Body",
  },
  {
    id: "review",
    durationMs: 15_000,
    titleKey: "strategyLibrary.demoFrame3Title",
    bodyKey: "strategyLibrary.demoFrame3Body",
  },
  {
    id: "schedule",
    durationMs: 15_000,
    titleKey: "strategyLibrary.demoFrame4Title",
    bodyKey: "strategyLibrary.demoFrame4Body",
  },
  {
    id: "next_run",
    durationMs: 20_000,
    titleKey: "strategyLibrary.demoFrame5Title",
    bodyKey: "strategyLibrary.demoFrame5Body",
  },
  {
    id: "boundary",
    durationMs: 10_000,
    titleKey: "strategyLibrary.demoFrame6Title",
    bodyKey: "strategyLibrary.demoFrame6Body",
  },
] as const satisfies readonly DemoFrame[];

export const DEMO_TOTAL_MS = DEMO_FRAMES.reduce(
  (total, frame) => total + frame.durationMs,
  0,
);