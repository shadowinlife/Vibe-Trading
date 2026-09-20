import './i18n';
import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { RouterProvider } from "react-router";
import { Toaster } from "sonner";
import { ErrorBoundary } from "./components/common/ErrorBoundary";
import i18n from "./i18n";
import { loadAuthMode } from "./lib/api";
import { router } from "./router";
import "highlight.js/styles/github-dark-dimmed.min.css";
import "./index.css";

const prefetchMiniEquityChart = () => {
  void import("@/components/charts/MiniEquityChart");
};

const idleWindow = window as Window & {
  requestIdleCallback?: (
    callback: IdleRequestCallback,
    options?: IdleRequestOptions,
  ) => number;
};

if (typeof idleWindow.requestIdleCallback === "function") {
  idleWindow.requestIdleCallback(prefetchMiniEquityChart, { timeout: 2000 });
} else {
  window.setTimeout(prefetchMiniEquityChart, 0);
}

/**
 * Resolve the backend capability flag before the first route renders (D19):
 * rendering earlier would flash the app shell — or wrongly redirect to a
 * login page — before we know whether user auth is even enabled.
 */
function Bootstrap() {
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let alive = true;
    void loadAuthMode().finally(() => {
      if (alive) setReady(true);
    });
    return () => {
      alive = false;
    };
  }, []);

  if (!ready) {
    return (
      <div className="flex h-screen items-center justify-center bg-background text-sm text-muted-foreground">
        {i18n.t("auth.checkingSession")}
      </div>
    );
  }
  return <RouterProvider router={router} />;
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ErrorBoundary>
      <Bootstrap />
      <Toaster position="bottom-right" richColors closeButton duration={3500} />
    </ErrorBoundary>
  </StrictMode>
);
