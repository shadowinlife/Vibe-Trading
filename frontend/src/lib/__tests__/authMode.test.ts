import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

async function loadModules() {
  vi.resetModules();
  const api = await import("../api");
  const { useAuthStore } = await import("@/stores/auth");
  return { loadAuthMode: api.loadAuthMode, useAuthStore };
}

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("loadAuthMode", () => {
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

  it("stores the flag the capability probe reports", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ user_auth: true })));

    const { loadAuthMode, useAuthStore } = await loadModules();
    await loadAuthMode();

    expect(useAuthStore.getState().userAuth).toBe(true);
    expect(useAuthStore.getState().modeLoaded).toBe(true);
  });

  it("defaults to false when the probe itself cannot be reached", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));

    const { loadAuthMode, useAuthStore } = await loadModules();
    await loadAuthMode();

    expect(useAuthStore.getState().userAuth).toBe(false);
    expect(useAuthStore.getState().modeLoaded).toBe(true);
  });

  it("defaults to false when the SPA catch-all answers with HTML", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response("<!doctype html><html><body>SPA</body></html>", {
          status: 200,
          headers: { "content-type": "text/html" },
        }),
      ),
    );

    const { loadAuthMode, useAuthStore } = await loadModules();
    await loadAuthMode();

    expect(useAuthStore.getState().userAuth).toBe(false);
  });

  it("defaults to false on a server error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ detail: "boom" }, 500)));

    const { loadAuthMode, useAuthStore } = await loadModules();
    await loadAuthMode();

    expect(useAuthStore.getState().userAuth).toBe(false);
  });

  it("treats a non-boolean flag as false rather than truthy", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ user_auth: "yes" })));

    const { loadAuthMode, useAuthStore } = await loadModules();
    await loadAuthMode();

    expect(useAuthStore.getState().userAuth).toBe(false);
  });
});