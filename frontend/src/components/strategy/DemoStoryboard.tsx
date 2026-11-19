import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { DEMO_FRAMES } from "./demoScript";
import { BUTTON_CLASS, FrameContent, LAST_INDEX } from "./DemoStoryboardFrames";

/** Namespaced so a private-mode failure can never break the page. */
export const DEMO_SEEN_STORAGE_KEY = "vibe.strategyDemo.seen";
const TICK_MS = 1000;

function hasReducedMotion(): boolean {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
    return false;
  }
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function readSeenFlag(): boolean {
  try {
    return window.localStorage.getItem(DEMO_SEEN_STORAGE_KEY) === "1";
  } catch {
    // Private mode or storage disabled: treat it as unseen so the demo
    // remains discoverable instead of failing silently.
    return false;
  }
}

function writeSeenFlag(): void {
  try {
    window.localStorage.setItem(DEMO_SEEN_STORAGE_KEY, "1");
  } catch {
    // Storage may be unavailable; the demo still works for this visit.
  }
}

export interface DemoStoryboardProps {
  /** Increment to open the panel from an external trigger (header button). */
  openRequest?: number;
}

/**
 * Self-contained 90-second walkthrough of the strategy lifecycle.
 *
 * It never fetches, never seeds, and never blocks the page: the last frame
 * only links into the library. A first visit opens it once; the seen flag is
 * namespaced and written defensively. Reduced-motion users get every frame
 * as a static storyboard with no timer at all.
 */
export function DemoStoryboard({ openRequest = 0 }: DemoStoryboardProps) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [frameIndex, setFrameIndex] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [elapsedMs, setElapsedMs] = useState(0);
  const [reduced, setReduced] = useState(hasReducedMotion);
  const initialised = useRef(false);

  const close = useCallback(() => {
    setPlaying(false);
    setOpen(false);
    setFrameIndex(0);
    setElapsedMs(0);
  }, []);

  useEffect(() => {
    if (initialised.current) return;
    initialised.current = true;
    if (readSeenFlag()) return;
    writeSeenFlag();
    setOpen(true);
    setPlaying(true);
  }, []);

  useEffect(() => {
    if (openRequest <= 0) return;
    setFrameIndex(0);
    setElapsedMs(0);
    setOpen(true);
    setPlaying(true);
  }, [openRequest]);

  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
      return;
    }
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    const onChange = (event: MediaQueryListEvent) => setReduced(event.matches);
    query.addEventListener?.("change", onChange);
    return () => query.removeEventListener?.("change", onChange);
  }, []);

  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") close();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open, close]);

  useEffect(() => {
    if (!open || reduced || !playing || frameIndex >= LAST_INDEX) return;
    const timer = window.setInterval(() => {
      setElapsedMs((previous) => previous + TICK_MS);
    }, TICK_MS);
    return () => window.clearInterval(timer);
  }, [open, reduced, playing, frameIndex]);

  useEffect(() => {
    if (!open || reduced || !playing) return;
    if (elapsedMs < DEMO_FRAMES[frameIndex].durationMs) return;
    if (frameIndex >= LAST_INDEX) {
      setPlaying(false);
      return;
    }
    const next = frameIndex + 1;
    setFrameIndex(next);
    setElapsedMs(0);
    if (next >= LAST_INDEX) setPlaying(false);
  }, [open, reduced, playing, frameIndex, elapsedMs]);

  function togglePlay() {
    if (reduced) return;
    if (playing) {
      setPlaying(false);
      return;
    }
    if (frameIndex >= LAST_INDEX) {
      setFrameIndex(0);
      setElapsedMs(0);
    }
    setPlaying(true);
  }

  function jumpTo(index: number) {
    if (reduced) return;
    setFrameIndex(index);
    setElapsedMs(0);
    setPlaying(false);
  }

  function skipToEnd() {
    setFrameIndex(LAST_INDEX);
    setElapsedMs(0);
    setPlaying(false);
  }

  function replay() {
    setFrameIndex(0);
    setElapsedMs(0);
    setPlaying(!reduced);
  }

  if (!open) return null;

  return (
    <section
      role="region"
      aria-label={t("strategyLibrary.demoTitle")}
      data-testid="demo-storyboard"
      className="fixed bottom-4 end-4 z-50 flex max-h-[80vh] w-[min(92vw,32rem)] flex-col overflow-y-auto rounded-lg border bg-card shadow-lg"
    >
      <header className="flex items-center justify-between gap-2 border-b px-3 py-2">
        <div className="min-w-0">
          <h2 className="truncate text-sm font-semibold">{t("strategyLibrary.demoTitle")}</h2>
          {!reduced && (
            <p className="text-[11px] text-muted-foreground">
              {t("strategyLibrary.demoFrameCounter", {
                current: frameIndex + 1,
                total: DEMO_FRAMES.length,
              })}
            </p>
          )}
        </div>
        <button
          type="button"
          onClick={close}
          aria-label={t("strategyLibrary.demoClose")}
          data-testid="demo-close"
          className={BUTTON_CLASS}
        >
          {t("strategyLibrary.demoClose")}
        </button>
      </header>

      {reduced ? (
        <div className="flex flex-col gap-2 p-3" data-testid="demo-static">
          <p className="text-[11px] text-muted-foreground">
            {t("strategyLibrary.demoReducedNote")}
          </p>
          {DEMO_FRAMES.map((frame, index) => (
            <FrameContent key={frame.id} index={index} staticFrame />
          ))}
        </div>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-1.5 border-b px-3 py-2">
            <button
              type="button"
              onClick={togglePlay}
              data-testid="demo-playpause"
              className={BUTTON_CLASS}
            >
              {t(
                playing
                  ? "strategyLibrary.demoPause"
                  : "strategyLibrary.demoPlay",
              )}
            </button>
            <button
              type="button"
              onClick={skipToEnd}
              data-testid="demo-skip"
              className={BUTTON_CLASS}
            >
              {t("strategyLibrary.demoSkip")}
            </button>
            <button
              type="button"
              onClick={replay}
              data-testid="demo-replay"
              className={BUTTON_CLASS}
            >
              {t("strategyLibrary.demoReplay")}
            </button>
            <div className="ms-auto flex items-center gap-1" role="group">
              {DEMO_FRAMES.map((frame, index) => (
                <button
                  key={frame.id}
                  type="button"
                  onClick={() => jumpTo(index)}
                  data-testid={`demo-dot-${index}`}
                  aria-label={t("strategyLibrary.demoGoToFrame", {
                    index: index + 1,
                  })}
                  aria-current={index === frameIndex ? "step" : undefined}
                  className={
                    index === frameIndex
                      ? "h-2 w-4 rounded-full bg-primary"
                      : "h-2 w-2 rounded-full bg-muted-foreground/40"
                  }
                />
              ))}
            </div>
          </div>
          <div className="p-3">
            <FrameContent index={frameIndex} staticFrame={false} />
          </div>
        </>
      )}
    </section>
  );
}