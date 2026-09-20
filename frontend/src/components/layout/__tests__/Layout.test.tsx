import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, beforeEach } from "vitest";
import { useAuthStore } from "@/stores/auth";
import { Layout } from "../Layout";

const sessions = [
  {
    session_id: "session-1",
    title: "A very long session title that must truncate",
  },
];

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => ({
      "app.version": "v0.1.15",
      "layout.agent": "Agent",
      "layout.alphaZoo": "Alpha Zoo",
      "layout.cancel": "Cancel",
      "layout.collapse": "Collapse",
      "layout.confirm": "Confirm",
      "layout.correlation": "Correlation Matrix",
      "layout.dark": "Dark",
      "layout.delete": "Delete",
      "layout.expand": "Expand",
      "layout.home": "Home",
      "layout.language": "Language",
      "layout.light": "Light",
      "layout.mainNavigation": "Main navigation",
      "layout.newChat": "New Chat",
      "layout.noSessions": "No sessions yet",
      "layout.rename": "Rename",
      "layout.reports": "Reports",
      "layout.runtime": "Runtime",
      "layout.sessions": "Sessions",
      "layout.settings": "Settings",
      "layout.sidebar": "Vibe-Trading sidebar",
      "layout.skipToMain": "Skip to main content",
      "auth.account": "Account",
      "auth.adminBadge": "Admin",
      "auth.logout": "Sign out",
      "auth.signedInAs": "Signed in as",
    })[key] ?? key,
    i18n: {
      language: "en",
      languages: ["en"],
      changeLanguage: vi.fn().mockResolvedValue(undefined),
    },
  }),
}));

vi.mock("@/hooks/useDarkMode", () => ({
  useDarkMode: () => ({ dark: false, toggle: vi.fn() }),
}));

const apiMock = vi.hoisted(() => ({
  listSessions: vi.fn(),
  deleteSession: vi.fn(),
  renameSession: vi.fn(),
  auth: { logout: vi.fn() },
}));

vi.mock("@/lib/api", () => ({ api: apiMock }));

vi.mock("@/stores/agent", () => ({
  useAgentStore: (selector: (state: {
    sseStatus: string;
    sseRetryAttempt: number;
    streamingSessionId: null;
  }) => unknown) => selector({
    sseStatus: "connected",
    sseRetryAttempt: 0,
    streamingSessionId: null,
  }),
}));

function renderLayout() {
  return render(
    <MemoryRouter initialEntries={["/agent"]}>
      <Routes>
        <Route element={<Layout />}>
          <Route path="/agent" element={<div>Agent content</div>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

describe("Layout accessibility", () => {
  beforeEach(() => {
    apiMock.listSessions.mockResolvedValue(sessions);
    apiMock.deleteSession.mockResolvedValue(undefined);
    apiMock.renameSession.mockResolvedValue(undefined);
  });

  it("labels landmarks, brand, main content, and the new-chat affordance", () => {
    renderLayout();

    expect(screen.getByRole("complementary", { name: "Vibe-Trading sidebar" })).toHaveClass("max-md:w-12");
    expect(screen.getByRole("navigation", { name: "Main navigation" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Vibe-Trading" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "New Chat" })).toHaveAttribute("title", "New Chat");
    expect(screen.getByText("Skip to main content")).toHaveAttribute("href", "#main");
    expect(screen.getByRole("main")).toHaveAttribute("id", "main");
    expect(screen.getByRole("main").parentElement).toHaveClass("relative");
    // Regression: <main> is a flex item inside a flex-col/overflow-hidden
    // parent. Without min-h-0, a flex item's default min-height:auto lets it
    // grow past its allotted space to fit tall content (e.g. a long
    // generated report) instead of respecting its own overflow-auto -- the
    // excess then gets hard-clipped by the parent's overflow-hidden with no
    // scrollbar at all, rather than scrolling into view.
    expect(screen.getByRole("main")).toHaveClass("flex-1", "min-h-0", "overflow-auto");
  });

  it("exposes session actions on keyboard focus and labels the rename input", async () => {
    renderLayout();

    const title = await screen.findByText(sessions[0].title);
    expect(title).toHaveClass("min-w-0", "truncate");

    const renameButton = screen.getByRole("button", { name: "Rename" });
    expect(renameButton.parentElement).toHaveClass("group-focus-within:opacity-100");
    fireEvent.click(renameButton);

    expect(screen.getByRole("textbox", { name: `Rename: ${sessions[0].title}` })).toHaveClass(
      "focus:ring-2",
      "focus:ring-primary/40",
    );
  });

  it("does not crash when localStorage access is blocked", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new DOMException("Blocked", "SecurityError");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("Blocked", "SecurityError");
    });

    expect(() => renderLayout()).not.toThrow();
  });

  it("uses button disclosure semantics for the language switcher", () => {
    renderLayout();

    const languageButton = screen.getByRole("button", { name: "Language" });
    expect(languageButton).toHaveAttribute("aria-expanded", "false");
    expect(languageButton).not.toHaveAttribute("aria-haspopup");
  });

  it("synchronizes the sidebar preference from another tab", () => {
    window.localStorage.setItem("qa-sidebar", "expanded");
    renderLayout();
    const sidebar = screen.getByRole("complementary", { name: "Vibe-Trading sidebar" });
    expect(sidebar).toHaveClass("w-64");

    window.localStorage.setItem("qa-sidebar", "collapsed");
    fireEvent(window, new StorageEvent("storage", { key: "qa-sidebar" }));

    expect(sidebar).toHaveClass("w-12");
  });
});

describe("Layout auth-capability branches", () => {
  const SIGNED_OUT = {
    userAuth: false,
    modeLoaded: true,
    token: null,
    username: null,
    role: null,
    displayName: null,
  };

  beforeEach(() => {
    apiMock.listSessions.mockResolvedValue(sessions);
    // The sidebar preference persists in localStorage across tests in this
    // file; start every case from the expanded sidebar.
    window.localStorage.setItem("qa-sidebar", "expanded");
    useAuthStore.setState({ ...SIGNED_OUT });
  });

  afterEach(() => {
    useAuthStore.setState({ ...SIGNED_OUT });
  });

  it("keeps the Settings entry visible while user auth is off", () => {
    renderLayout();

    expect(screen.getByRole("link", { name: "Settings" })).toBeInTheDocument();
  });

  it("hides the Settings entry from a signed-in non-admin", () => {
    useAuthStore.setState({ userAuth: true, role: "user", username: "alice" });

    renderLayout();

    expect(screen.queryByRole("link", { name: "Settings" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Agent" })).toBeInTheDocument();
  });

  it("keeps the Settings entry for an admin", () => {
    useAuthStore.setState({ userAuth: true, role: "admin", username: "alice" });

    renderLayout();

    expect(screen.getByRole("link", { name: "Settings" })).toBeInTheDocument();
  });

  it("shows no account menu without a session", () => {
    renderLayout();

    expect(screen.queryByRole("button", { name: "Account" })).not.toBeInTheDocument();
  });

  it("shows the signed-in account and signs out", async () => {
    useAuthStore.setState({
      userAuth: true,
      role: "admin",
      username: "alice",
      displayName: "Alice",
    });

    renderLayout();

    const account = screen.getByRole("button", { name: "Account" });
    expect(account).toHaveTextContent("Alice");
    expect(account).toHaveTextContent("Admin");

    fireEvent.click(account);
    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));

    await waitFor(() => expect(apiMock.auth.logout).toHaveBeenCalledTimes(1));
    expect(useAuthStore.getState().username).toBeNull();
  });
});
