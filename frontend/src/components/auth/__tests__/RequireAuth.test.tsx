import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api";
import { expireSession, useAuthStore } from "@/stores/auth";
import { RequireAdmin, RequireAuth } from "../RequireAuth";

const meMock = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: { auth: { me: meMock } } };
});

const IDLE_STATE = {
  userAuth: false,
  modeLoaded: true,
  token: null,
  username: null,
  role: null,
  displayName: null,
};

async function signedIn(role: "user" | "admin" = "user") {
  useAuthStore.getState().setSession({
    token: "session-abc",
    username: "alice",
    role,
    displayName: null,
  });
}

function renderGuarded() {
  return render(
    <MemoryRouter initialEntries={["/"]}>
      <Routes>
        <Route path="/login" element={<div>login page</div>} />
        <Route
          path="/"
          element={
            <RequireAuth>
              <div>protected content</div>
            </RequireAuth>
          }
        />
      </Routes>
    </MemoryRouter>,
  );
}

function renderOperatorRoute() {
  return render(
    <MemoryRouter initialEntries={["/settings"]}>
      <Routes>
        <Route path="/" element={<div>home</div>} />
        <Route
          path="/settings"
          element={
            <RequireAdmin>
              <div>operator content</div>
            </RequireAdmin>
          }
        />
      </Routes>
    </MemoryRouter>,
  );
}

describe("RequireAuth", () => {
  beforeEach(() => {
    meMock.mockReset();
    useAuthStore.setState({ ...IDLE_STATE });
  });

  afterEach(() => {
    useAuthStore.setState({ ...IDLE_STATE });
  });

  it("bypasses the guard entirely while user auth is off", () => {
    renderGuarded();

    expect(screen.getByText("protected content")).toBeInTheDocument();
    expect(meMock).not.toHaveBeenCalled();
  });

  it("redirects to /login when user auth is on and no token is stored", async () => {
    useAuthStore.getState().setUserAuth(true);

    renderGuarded();

    expect(await screen.findByText("login page")).toBeInTheDocument();
    expect(meMock).not.toHaveBeenCalled();
  });

  it("validates the stored token against /auth/me before rendering the app", async () => {
    useAuthStore.getState().setUserAuth(true);
    await signedIn("admin");
    meMock.mockResolvedValue({ username: "alice", role: "admin", display_name: "Alice" });

    renderGuarded();

    expect(await screen.findByText("protected content")).toBeInTheDocument();
    expect(meMock).toHaveBeenCalledTimes(1);
    expect(useAuthStore.getState().role).toBe("admin");
    expect(useAuthStore.getState().displayName).toBe("Alice");
  });

  it("normalizes an unknown role down to user rather than trusting it", async () => {
    useAuthStore.getState().setUserAuth(true);
    await signedIn("user");
    meMock.mockResolvedValue({ username: "alice", role: "root", display_name: null });

    renderGuarded();

    expect(await screen.findByText("protected content")).toBeInTheDocument();
    expect(useAuthStore.getState().role).toBe("user");
  });

  it("clears a rejected session and sends the user to /login", async () => {
    useAuthStore.getState().setUserAuth(true);
    await signedIn();
    meMock.mockRejectedValue(new ApiError("Invalid or expired session", 401));

    renderGuarded();

    expect(await screen.findByText("login page")).toBeInTheDocument();
    expect(useAuthStore.getState().token).toBeNull();
  });

  it("keeps rendering the app when /auth/me fails for transport reasons", async () => {
    useAuthStore.getState().setUserAuth(true);
    await signedIn();
    meMock.mockRejectedValue(new TypeError("Failed to fetch"));

    renderGuarded();

    expect(await screen.findByText("protected content")).toBeInTheDocument();
    expect(useAuthStore.getState().token).toBe("session-abc");
  });

  it("keeps the session when /auth/me is forbidden rather than refused", async () => {
    useAuthStore.getState().setUserAuth(true);
    await signedIn();
    meMock.mockRejectedValue(new ApiError("Cross-site request denied", 403));

    renderGuarded();

    // A 403 is a permission verdict (admin gate / cross-site rejection); only
    // a 401 may clear a session and redirect.
    expect(await screen.findByText("protected content")).toBeInTheDocument();
    expect(useAuthStore.getState().token).toBe("session-abc");
    expect(screen.queryByText("login page")).not.toBeInTheDocument();
  });

  it("holds the loading state until the capability flag resolves", () => {
    useAuthStore.setState({ modeLoaded: false });

    renderGuarded();

    expect(screen.queryByText("protected content")).not.toBeInTheDocument();
    expect(meMock).not.toHaveBeenCalled();
  });
});

describe("RequireAdmin", () => {
  beforeEach(() => {
    meMock.mockReset();
    useAuthStore.setState({ ...IDLE_STATE });
  });

  afterEach(() => {
    useAuthStore.setState({ ...IDLE_STATE });
  });

  it("leaves the operator route reachable while user auth is off", () => {
    renderOperatorRoute();

    expect(screen.getByText("operator content")).toBeInTheDocument();
  });

  it("redirects a signed-in non-admin away from the operator route", async () => {
    useAuthStore.getState().setUserAuth(true);
    await signedIn("user");

    renderOperatorRoute();

    expect(await screen.findByText("home")).toBeInTheDocument();
    expect(screen.queryByText("operator content")).not.toBeInTheDocument();
  });

  it("lets an admin through", async () => {
    useAuthStore.getState().setUserAuth(true);
    await signedIn("admin");

    renderOperatorRoute();

    expect(screen.getByText("operator content")).toBeInTheDocument();
  });
});

describe("auth store session expiry", () => {
  afterEach(() => {
    useAuthStore.setState({ ...IDLE_STATE });
  });

  it("waits for the flag before it clears anything", async () => {
    await signedIn();
    expireSession();
    expect(useAuthStore.getState().token).toBe("session-abc");
  });

  it("clears the session once user auth is on", async () => {
    useAuthStore.getState().setUserAuth(true);
    await signedIn();
    expireSession();
    expect(useAuthStore.getState().token).toBeNull();
    expect(useAuthStore.getState().username).toBeNull();
  });

  it("is a no-op when there is no session to expire", () => {
    useAuthStore.getState().setUserAuth(true);
    expect(() => expireSession()).not.toThrow();
    expect(useAuthStore.getState().token).toBeNull();
  });
});