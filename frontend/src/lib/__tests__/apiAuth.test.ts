import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  AuthTicketError,
  authHeaders,
  clearLegacyApiAuthKey,
  getApiAuthKey,
  getSessionToken,
  setApiAuthKey,
  setSessionToken,
  withAuthTicket,
} from "../apiAuth";

const SESSION_KEY = "vibe_trading_session_token";
const LEGACY_KEY = "vibe_trading_api_auth_key";

describe("apiAuth", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  describe("session token", () => {
    it("returns empty string when nothing stored", () => {
      expect(getSessionToken()).toBe("");
    });

    it("stores the token under the session key, not the legacy one", () => {
      setSessionToken("tok-123");
      expect(localStorage.getItem(SESSION_KEY)).toBe("tok-123");
      expect(localStorage.getItem(LEGACY_KEY)).toBeNull();
      expect(getSessionToken()).toBe("tok-123");
    });

    it("trims the stored value", () => {
      setSessionToken("  tok-123  ");
      expect(getSessionToken()).toBe("tok-123");
    });

    it("removes the token when the value is empty or whitespace", () => {
      setSessionToken("tok-123");
      setSessionToken("   ");
      expect(localStorage.getItem(SESSION_KEY)).toBeNull();
    });

    it("returns empty string when storage access is blocked", () => {
      vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
        throw new DOMException("blocked", "SecurityError");
      });
      expect(getSessionToken()).toBe("");
    });

    it("does not throw when storage writes are blocked", () => {
      vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
        throw new DOMException("blocked", "SecurityError");
      });
      expect(() => setSessionToken("tok-123")).not.toThrow();
    });
  });

  describe("legacy shared key", () => {
    it("stays readable for flag-off deployments", () => {
      localStorage.setItem(LEGACY_KEY, "my-secret");
      expect(getApiAuthKey()).toBe("my-secret");
    });

    it("is cleared once the backend enables user auth", () => {
      localStorage.setItem(LEGACY_KEY, "my-secret");
      clearLegacyApiAuthKey();
      expect(localStorage.getItem(LEGACY_KEY)).toBeNull();
    });

    it("still trims and clears through setApiAuthKey", () => {
      setApiAuthKey("  abc-123  ");
      expect(localStorage.getItem(LEGACY_KEY)).toBe("abc-123");
      setApiAuthKey("");
      expect(localStorage.getItem(LEGACY_KEY)).toBeNull();
    });
  });

  describe("authHeaders", () => {
    it("returns empty object when no credential set", () => {
      expect(authHeaders()).toEqual({});
    });

    it("prefers the session token over the legacy key", () => {
      setApiAuthKey("shared-key");
      setSessionToken("session-token");
      expect(authHeaders()).toEqual({ Authorization: "Bearer session-token" });
    });

    it("falls back to the legacy key when no session token exists", () => {
      setApiAuthKey("shared-key");
      expect(authHeaders()).toEqual({ Authorization: "Bearer shared-key" });
    });
  });

  describe("withAuthTicket", () => {
    it("returns url unchanged and makes no request when no credential (dev/loopback)", async () => {
      const fetchSpy = vi.fn();
      vi.stubGlobal("fetch", fetchSpy);
      await expect(withAuthTicket("http://api/stream")).resolves.toBe("http://api/stream");
      expect(fetchSpy).not.toHaveBeenCalled();
    });

    it("mints a ticket via header-authed POST and appends ?ticket=", async () => {
      setSessionToken("session-token");
      const fetchSpy = vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ ticket: "TICKET-123" }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      );
      vi.stubGlobal("fetch", fetchSpy);

      const url = await withAuthTicket("http://api/stream");

      expect(url).toBe("http://api/stream?ticket=TICKET-123");
      expect(fetchSpy).toHaveBeenCalledTimes(1);
      const [path, init] = fetchSpy.mock.calls[0];
      expect(path).toBe("/auth/sse-ticket");
      expect(init.method).toBe("POST");
      expect(init.headers).toEqual({ Authorization: "Bearer session-token" });
    });

    it("also mints a ticket for the legacy shared key", async () => {
      setApiAuthKey("shared-key");
      const fetchSpy = vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ ticket: "T" }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      );
      vi.stubGlobal("fetch", fetchSpy);

      await withAuthTicket("http://api/stream");

      expect(fetchSpy.mock.calls[0][1].headers).toEqual({
        Authorization: "Bearer shared-key",
      });
    });

    it("never puts the raw credential in the returned URL", async () => {
      setSessionToken("super-secret-token");
      vi.stubGlobal(
        "fetch",
        vi.fn().mockResolvedValue(
          new Response(JSON.stringify({ ticket: "one-shot" }), {
            status: 200,
            headers: { "content-type": "application/json" },
          }),
        ),
      );
      const url = await withAuthTicket("http://api/stream");
      expect(url).not.toContain("super-secret-token");
      expect(url).not.toContain("api_key=");
      expect(url).toContain("ticket=one-shot");
    });

    it("joins with & when the url already has a query string", async () => {
      setSessionToken("tok");
      vi.stubGlobal(
        "fetch",
        vi.fn().mockResolvedValue(
          new Response(JSON.stringify({ ticket: "t1" }), {
            status: 200,
            headers: { "content-type": "application/json" },
          }),
        ),
      );
      const url = await withAuthTicket("http://api/stream?replay=active");
      expect(url).toBe("http://api/stream?replay=active&ticket=t1");
    });

    it("throws a typed error carrying the status when the ticket endpoint rejects", async () => {
      setSessionToken("tok");
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("", { status: 401 })));

      const error = await withAuthTicket("http://api/stream").catch((thrown: unknown) => thrown);

      expect(error).toBeInstanceOf(AuthTicketError);
      expect(error).toMatchObject({
        status: 401,
        message: "Failed to obtain SSE ticket (HTTP 401)",
      });
    });

    it("throws when the response is missing a ticket", async () => {
      setSessionToken("tok");
      vi.stubGlobal(
        "fetch",
        vi.fn().mockResolvedValue(
          new Response(JSON.stringify({}), {
            status: 200,
            headers: { "content-type": "application/json" },
          }),
        ),
      );
      await expect(withAuthTicket("http://api/stream")).rejects.toThrow(/missing ticket/);
    });
  });
});