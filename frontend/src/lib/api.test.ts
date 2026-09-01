import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, api, wsUrl } from "./api";

function mockFetch(status: number, body: unknown, ok = status < 400) {
  const fn = vi.fn().mockResolvedValue({
    ok,
    status,
    json: async () => body,
  } as Response);
  vi.stubGlobal("fetch", fn);
  return fn;
}

afterEach(() => vi.unstubAllGlobals());

describe("error handling", () => {
  it("surfaces the server's own message rather than a generic one", async () => {
    // The backend writes these for the user ("start llama-server…"), so
    // replacing them with "Request failed" loses the actionable half.
    mockFetch(503, {
      error: {
        code: "model_unavailable",
        message: "The coaching model isn't running. Start llama-server…",
      },
    });

    await expect(api.status()).rejects.toThrowError(ApiError);
    await expect(api.status()).rejects.toMatchObject({
      code: "model_unavailable",
      status: 503,
      message: expect.stringContaining("llama-server"),
    });
  });

  it("falls back to a status message when the body is not our envelope", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 502,
        json: async () => {
          throw new SyntaxError("not json");
        },
      } as unknown as Response),
    );

    await expect(api.status()).rejects.toMatchObject({
      code: "http_error",
      status: 502,
      message: expect.stringContaining("502"),
    });
  });

  it("returns undefined for 204 without trying to parse a body", async () => {
    const json = vi.fn();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, status: 204, json } as unknown as Response),
    );

    await expect(api.endSession("abc")).resolves.toBeUndefined();
    expect(json).not.toHaveBeenCalled();
  });

  it("passes successful payloads straight through", async () => {
    mockFetch(200, { live_available: true, corpus_chunks: 1057 });
    await expect(api.status()).resolves.toMatchObject({ corpus_chunks: 1057 });
  });
});

describe("wsUrl", () => {
  const setLocation = (href: string) => {
    const url = new URL(href);
    vi.stubGlobal("window", {
      location: { protocol: url.protocol, host: url.host },
    });
  };

  it("uses ws over http", () => {
    setLocation("http://localhost:5173/");
    expect(wsUrl("/ws/live")).toBe("ws://localhost:5173/ws/live");
  });

  it("uses wss over https, so a deployed build is not blocked as mixed content", () => {
    setLocation("https://coach.example.org/");
    expect(wsUrl("/ws/knowledge")).toBe("wss://coach.example.org/ws/knowledge");
  });

  it("encodes query parameters", () => {
    setLocation("http://localhost:5173/");
    expect(wsUrl("/ws/live", { session_id: "a b&c" })).toBe(
      "ws://localhost:5173/ws/live?session_id=a+b%26c",
    );
  });

  it("omits the question mark when there are no parameters", () => {
    setLocation("http://localhost:5173/");
    expect(wsUrl("/ws/live", {})).toBe("ws://localhost:5173/ws/live");
  });
});
