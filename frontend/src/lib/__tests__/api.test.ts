import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api";

async function loadApiModule() {
  vi.resetModules();
  return import("../api");
}

describe("api request helper", () => {
  beforeEach(() => {
    vi.stubGlobal("localStorage", {
      getItem: vi.fn(() => ""),
      setItem: vi.fn(),
      removeItem: vi.fn(),
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.resetModules();
  });

  it("rejects non-JSON responses with a descriptive error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response("<!doctype html><html><body>SPA</body></html>", {
          status: 200,
          headers: { "content-type": "text/html" },
        }),
      ),
    );

    const { api } = await loadApiModule();

    await expect(api.getChannelStatus()).rejects.toMatchObject({
      name: "ApiError",
      status: 200,
      message: expect.stringContaining("Expected JSON from /channels/status, got text/html"),
    } satisfies Partial<ApiError>);
  });

  it("posts an authenticated connector verification request with an encoded profile id", async () => {
    vi.stubGlobal("localStorage", {
      getItem: vi.fn(() => "remote-test-key"),
      setItem: vi.fn(),
      removeItem: vi.fn(),
    });
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ status: "ok", connection_state: "connected" }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { api } = await loadApiModule();

    await expect(api.verifyConnector("longbridge/live sdk")).resolves.toMatchObject({ status: "ok" });
    expect(fetchMock).toHaveBeenCalledWith(
      "/live/connectors/longbridge%2Flive%20sdk/verify?force=true",
      expect.objectContaining({
        method: "POST",
        headers: expect.objectContaining({ Authorization: "Bearer remote-test-key" }),
      }),
    );
  });

  it("sends the stored API key when fetching a correlation matrix", async () => {
    vi.stubGlobal("localStorage", {
      getItem: vi.fn(() => "remote-test-key"),
      setItem: vi.fn(),
      removeItem: vi.fn(),
    });
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ labels: ["A", "B"], matrix: [[1, 0], [0, 1]] }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { api } = await loadApiModule();

    await expect(api.getCorrelation("A,B", 90, "pearson")).resolves.toEqual({
      labels: ["A", "B"],
      matrix: [[1, 0], [0, 1]],
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "/correlation?codes=A%2CB&days=90&method=pearson",
      expect.objectContaining({
        headers: expect.objectContaining({ Authorization: "Bearer remote-test-key" }),
      }),
    );
  });

  it("sends the stored API key when fetching a correlation regime timeline", async () => {
    vi.stubGlobal("localStorage", {
      getItem: vi.fn(() => "remote-test-key"),
      setItem: vi.fn(),
      removeItem: vi.fn(),
    });
    const regime = {
      labels: ["A", "B"],
      dates: ["2024-01-01"],
      density: [0.5],
      smoothed: [0.5],
      fused: [0],
      episodes: [],
      params: {},
    };
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(regime), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { api } = await loadApiModule();

    await expect(api.getCorrelationRegime("A,B", 90)).resolves.toEqual(regime);
    expect(fetchMock).toHaveBeenCalledWith(
      "/correlation/regime?codes=A%2CB&days=90",
      expect.objectContaining({
        headers: expect.objectContaining({ Authorization: "Bearer remote-test-key" }),
      }),
    );
  });

  it("preserves the backend detail on a rejected request instead of masking it", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: "Cross-site request denied" }), {
          status: 403,
          headers: { "content-type": "application/json" },
        }),
      ),
    );

    const { api } = await loadApiModule();

    // Regression: masking this detail once sent debugging down an API-key
    // path for what was a reverse-proxy port bug.
    await expect(api.getCorrelation("A,B", 90, "pearson")).rejects.toMatchObject({
      status: 403,
      message: "Cross-site request denied",
    } satisfies Partial<ApiError>);
  });

  it("falls back to the active locale's auth wording when the backend sends no detail", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("", { status: 401 })));

    const apiModule = await loadApiModule();
    const { default: i18n } = await import("@/i18n");
    await i18n.changeLanguage("en");

    await expect(apiModule.api.getCorrelation("A,B", 90, "pearson")).rejects.toMatchObject({
      status: 401,
      message: i18n.t("agent.authRequired"),
    } satisfies Partial<ApiError>);
  });

  it("clears the session on a 401 only once user auth is enabled", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("", { status: 401 })));

    const { api } = await loadApiModule();
    const { useAuthStore } = await import("@/stores/auth");
    useAuthStore.getState().setSession({
      token: "session-abc",
      username: "alice",
      role: "user",
      displayName: null,
    });

    // Given user auth off (the default), the token must survive.
    await expect(api.getCorrelation("A,B", 90, "pearson")).rejects.toMatchObject({ status: 401 });
    expect(useAuthStore.getState().token).toBe("session-abc");

    // When user auth is on, the same rejection ends the session.
    useAuthStore.getState().setUserAuth(true);
    await expect(api.getCorrelation("A,B", 90, "pearson")).rejects.toMatchObject({ status: 401 });
    expect(useAuthStore.getState().token).toBeNull();
  });

  it("keeps the session when an admin gate forbids a signed-in user", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: "Administrator access required" }), {
          status: 403,
          headers: { "content-type": "application/json" },
        }),
      ),
    );

    const { api } = await loadApiModule();
    const { useAuthStore } = await import("@/stores/auth");
    useAuthStore.getState().setUserAuth(true);
    useAuthStore.getState().setSession({
      token: "session-abc",
      username: "alice",
      role: "user",
      displayName: null,
    });

    // Regression: 403 means "authenticated but not allowed" (admin_auth.py),
    // never "your session is gone" — logging this user out would drop them
    // into a login loop that cannot fix the missing role.
    await expect(api.getCorrelation("A,B", 90, "pearson")).rejects.toMatchObject({
      status: 403,
      message: "Administrator access required",
    } satisfies Partial<ApiError>);
    expect(useAuthStore.getState().token).toBe("session-abc");
    expect(useAuthStore.getState().username).toBe("alice");
  });

  it("does not claim an expired session on a 403 without a backend detail", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("", { status: 403 })));

    const { api } = await loadApiModule();
    const { useAuthStore } = await import("@/stores/auth");
    useAuthStore.getState().setUserAuth(true);
    useAuthStore.getState().setSession({
      token: "session-abc",
      username: "alice",
      role: "user",
      displayName: null,
    });

    await expect(api.getCorrelation("A,B", 90, "pearson")).rejects.toMatchObject({
      status: 403,
      message: "HTTP 403",
    } satisfies Partial<ApiError>);
    expect(useAuthStore.getState().token).toBe("session-abc");
  });

  it("keeps the legacy 403 wording and the session while user auth is off", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("", { status: 403 })));

    const { api } = await loadApiModule();
    const { useAuthStore } = await import("@/stores/auth");
    const { default: i18n } = await import("@/i18n");
    await i18n.changeLanguage("en");
    // Flag-off cannot hold a session in practice; the fixture proves the
    // logout channel still refuses to clear one at flag-off (D19).
    useAuthStore.getState().setSession({
      token: "legacy-token",
      username: "legacy",
      role: "user",
      displayName: null,
    });

    await expect(api.getCorrelation("A,B", 90, "pearson")).rejects.toMatchObject({
      status: 403,
      message: i18n.t("agent.authRequired"),
    } satisfies Partial<ApiError>);
    expect(useAuthStore.getState().token).toBe("legacy-token");
  });
});
