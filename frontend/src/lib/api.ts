import type {
  Citation,
  ProgressOut,
  SessionDetail,
  SessionSummary,
  SystemStatus,
} from "./types";

/** Backend error envelope: {"error": {code, message, ...}}. */
export class ApiError extends Error {
  constructor(
    message: string,
    readonly code: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    ...init,
  });

  if (!response.ok) {
    // Surface the server's own wording — it is written for the user, and
    // replacing it with a generic string loses the "what happens next" half.
    let message = `Request failed (${response.status})`;
    let code = "http_error";
    try {
      const body = await response.json();
      if (body?.error) {
        message = body.error.message ?? message;
        code = body.error.code ?? code;
      }
    } catch {
      // Non-JSON error body; the status-based message stands.
    }
    throw new ApiError(message, code, response.status);
  }

  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export const api = {
  status: () => request<SystemStatus>("/api/status"),

  createSession: () => request<SessionSummary>("/api/sessions", { method: "POST" }),

  listSessions: () => request<SessionSummary[]>("/api/sessions"),

  /** A past session with all of its turns, for replaying it in the UI. */
  getSession: (id: string) => request<SessionDetail>(`/api/sessions/${id}`),

  deleteSession: (id: string) =>
    request<void>(`/api/sessions/${id}`, { method: "DELETE" }),

  progress: () => request<ProgressOut>("/api/sessions/progress"),

  endSession: (id: string) =>
    request<SessionSummary>(`/api/sessions/${id}/end`, { method: "POST" }),

  searchCorpus: (q: string) =>
    request<Citation[]>(`/api/corpus/search?q=${encodeURIComponent(q)}`),

  ingestCorpus: () =>
    request<{ files: number; chunks: number }>("/api/corpus/ingest", {
      method: "POST",
    }),
};

/** Build a same-origin WebSocket URL, honouring https in deployment. */
export function wsUrl(path: string, params: Record<string, string> = {}): string {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const query = new URLSearchParams(params).toString();
  return `${protocol}//${window.location.host}${path}${query ? `?${query}` : ""}`;
}
