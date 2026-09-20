import { Suspense, lazy, type ComponentType } from "react";
import { createBrowserRouter } from "react-router";
import { RequireAdmin, RequireAuth } from "@/components/auth/RequireAuth";
import { Layout } from "@/components/layout/Layout";

const Login = lazy(() => import("@/pages/Login").then((m) => ({ default: m.Login })));

const Home = lazy(() => import("@/pages/Home").then((m) => ({ default: m.Home })));
const Agent = lazy(() => import("@/pages/Agent").then((m) => ({ default: m.Agent })));
const RunDetail = lazy(() =>
  import("@/pages/RunDetail").then((m) => ({ default: m.RunDetail })),
);
const Compare = lazy(() =>
  import("@/pages/Compare").then((m) => ({ default: m.Compare })),
);
const Settings = lazy(() =>
  import("@/pages/Settings").then((m) => ({ default: m.Settings })),
);
const Runtime = lazy(() =>
  import("@/pages/Runtime").then((m) => ({ default: m.Runtime })),
);
const Scheduled = lazy(() =>
  import("@/pages/Scheduled").then((m) => ({ default: m.Scheduled })),
);
const Reports = lazy(() =>
  import("@/pages/Reports").then((m) => ({ default: m.Reports })),
);
const Portfolio = lazy(() =>
  import("@/pages/Portfolio").then((m) => ({ default: m.Portfolio })),
);
const Correlation = lazy(() =>
  import("@/pages/Correlation").then((m) => ({ default: m.Correlation })),
);
const AlphaZoo = lazy(() =>
  import("@/pages/AlphaZoo").then((m) => ({ default: m.AlphaZoo })),
);
const OptionsLab = lazy(() =>
  import("@/pages/OptionsLab").then((m) => ({ default: m.OptionsLab })),
);

function PageLoader() {
  return (
    <div className="flex h-[60vh] items-center justify-center text-muted-foreground">
      Loading…
    </div>
  );
}

function wrap(Component: ComponentType) {
  return (
    <Suspense fallback={<PageLoader />}>
      <Component />
    </Suspense>
  );
}

/** `wrap` plus the operator-only gate that hides the Settings surface. */
function wrapAdmin(Component: ComponentType) {
  return (
    <Suspense fallback={<PageLoader />}>
      <RequireAdmin>
        <Component />
      </RequireAdmin>
    </Suspense>
  );
}

export const router = createBrowserRouter([
  // Outside the Layout shell — no sidebar or session list before a session exists.
  { path: "/login", element: wrap(Login) },
  {
    element: (
      <RequireAuth>
        <Layout />
      </RequireAuth>
    ),
    children: [
      { path: "/", element: wrap(Agent) },
      { path: "/about", element: wrap(Home) },
      { path: "/agent", element: wrap(Agent) },
      { path: "/runtime", element: wrap(Runtime) },
      { path: "/scheduled", element: wrap(Scheduled) },
      { path: "/reports", element: wrap(Reports) },
      { path: "/portfolio", element: wrap(Portfolio) },
      { path: "/settings", element: wrapAdmin(Settings) },
      { path: "/runs/:runId", element: wrap(RunDetail) },
      { path: "/compare", element: wrap(Compare) },
      { path: "/correlation", element: wrap(Correlation) },
      { path: "/options", element: wrap(OptionsLab) },
      { path: "/alpha-zoo", element: wrap(AlphaZoo) },
      { path: "/alpha-zoo/bench", element: wrap(AlphaZoo) },
      { path: "/alpha-zoo/compare", element: wrap(AlphaZoo) },
      { path: "/alpha-zoo/:alphaId", element: wrap(AlphaZoo) },
    ],
  },
]);
