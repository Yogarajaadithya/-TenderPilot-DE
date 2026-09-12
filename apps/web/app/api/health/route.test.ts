import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const VALID_READY_BODY = {
  status: "ready",
  app_env: "development",
  dependencies: { database: "not_configured" },
};

describe("GET /api/health", () => {
  const originalBackendUrl = process.env.BACKEND_URL;
  const originalFetch = global.fetch;

  beforeEach(() => {
    vi.resetModules();
    process.env.BACKEND_URL = "http://127.0.0.1:8000";
  });

  afterEach(() => {
    process.env.BACKEND_URL = originalBackendUrl;
    global.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("returns misconfigured when BACKEND_URL is unset", async () => {
    delete process.env.BACKEND_URL;
    const { GET } = await import("./route");

    const response = await GET();
    const body = await response.json();

    expect(response.status).toBe(500);
    expect(body.status).toBe("misconfigured");
  });

  it("returns connected with the backend body when the backend responds with a valid readiness shape", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => VALID_READY_BODY,
    }) as unknown as typeof fetch;

    const { GET } = await import("./route");
    const response = await GET();
    const body = await response.json();

    expect(response.status).toBe(200);
    expect(body.status).toBe("connected");
    expect(body.backend).toEqual(VALID_READY_BODY);
  });

  it("returns unavailable when the backend responds with a non-OK status", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 503,
      json: async () => ({}),
    }) as unknown as typeof fetch;

    const { GET } = await import("./route");
    const response = await GET();
    const body = await response.json();

    expect(response.status).toBe(502);
    expect(body.status).toBe("unavailable");
  });

  it("returns unavailable when the fetch throws (backend unreachable)", async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error("ECONNREFUSED")) as unknown as typeof fetch;

    const { GET } = await import("./route");
    const response = await GET();
    const body = await response.json();

    expect(response.status).toBe(502);
    expect(body.status).toBe("unavailable");
  });

  it("returns unavailable when the fetch is aborted by the timeout", async () => {
    global.fetch = vi.fn().mockRejectedValue(
      Object.assign(new Error("aborted"), { name: "AbortError" }),
    ) as unknown as typeof fetch;

    const { GET } = await import("./route");
    const response = await GET();
    const body = await response.json();

    expect(response.status).toBe(502);
    expect(body.status).toBe("unavailable");
    expect(body.message).toMatch(/did not respond within/i);
  });

  it("returns unavailable when the backend's JSON body cannot be parsed", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => {
        throw new SyntaxError("Unexpected token < in JSON");
      },
    }) as unknown as typeof fetch;

    const { GET } = await import("./route");
    const response = await GET();
    const body = await response.json();

    expect(response.status).toBe(502);
    expect(body.status).toBe("unavailable");
    expect(body.message).toMatch(/not valid JSON/i);
  });

  it.each([
    ["a body missing required fields", { status: "ready" }],
    ["a body with the wrong type for a field", { ...VALID_READY_BODY, app_env: 123 }],
    ["a body with an invalid status enum value", { ...VALID_READY_BODY, status: "totally-fine" }],
    [
      "a body whose dependencies map has a non-string value",
      { ...VALID_READY_BODY, dependencies: { database: 42 } },
    ],
    [
      // Regression test: `typeof [] === "object"` in JavaScript, so this
      // case previously slipped past isReadinessResponse()'s "is it an
      // object" check and got labelled connected.
      "an array instead of an object for dependencies",
      { ...VALID_READY_BODY, dependencies: ["not_configured", "healthy"] },
    ],
    ["a non-object body (e.g. an HTML error page mistakenly parsed)", "<html>502</html>"],
  ])(
    "does NOT label the response connected when the backend sends %s",
    async (_label, malformedBody) => {
      global.fetch = vi.fn().mockResolvedValue({
        ok: true,
        json: async () => malformedBody,
      }) as unknown as typeof fetch;

      const { GET } = await import("./route");
      const response = await GET();
      const body = await response.json();

      expect(response.status).toBe(502);
      expect(body.status).toBe("unavailable");
      expect(body.message).toMatch(/did not match the expected readiness contract/i);
    },
  );
});
