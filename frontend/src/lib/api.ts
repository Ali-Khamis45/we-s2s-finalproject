import { authHeader, refreshAccessToken } from "./auth";
import type {
  AuthResponse,
  Citation,
  ProgressOut,
  SessionDetail,
  SessionSummary,
  SystemStatus,
  UserOut,
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

async function send(path: string, init?: RequestInit): Promise<Response> {
  return fetch(path, {
    credentials: "include",
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...authHeader(),
      ...(init?.headers ?? {}),
    },
  });
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response = await send(path, init);

  // Exactly one refresh and one retry, and only for an expired token. Retrying
  // on every 401 would loop when the refresh itself is dead; the server marks
  // expiry with its own code precisely so the client can tell them apart.
  if (response.status === 401) {
    let expired = false;
    try {
      expired = (await response.clone().json())?.error?.code === "token_expired";
    } catch {
      /* non-JSON body: treat as a hard 401 */
    }
    if (expired && (await refreshAccessToken())) {
      response = await send(path, init);
    }
  }

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

export const auth = {
  register: (email: string, password: string, display_name?: string) =>
    request<AuthResponse>("/api/auth/register", {
      method: "POST",
      body: JSON.stringify({ email, password, display_name }),
    }),

  login: (email: string, password: string) =>
    request<AuthResponse>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }),

  logout: () => request<void>("/api/auth/logout", { method: "POST" }),

  logoutAll: () => request<void>("/api/auth/logout-all", { method: "POST" }),

  me: () => request<UserOut>("/api/auth/me"),

  updateMe: (patch: { display_name?: string; memory_enabled?: boolean }) =>
    request<UserOut>("/api/auth/me", { method: "PATCH", body: JSON.stringify(patch) }),

  changePassword: (current_password: string, new_password: string) =>
    request<void>("/api/auth/me/password", {
      method: "POST",
      body: JSON.stringify({ current_password, new_password }),
    }),

  deleteAccount: (current_password: string) =>
    request<void>("/api/auth/me", {
      method: "DELETE",
      body: JSON.stringify({ current_password }),
    }),

  exportUrl: "/api/auth/me/export",
};

export const api = {
  status: () => request<SystemStatus>("/api/status"),

  createSession: () => request<SessionSummary>("/api/sessions", { method: "POST" }),

  listSessions: () => request<SessionSummary[]>("/api/sessions"),

  /** A past session with all of its turns, for replaying it in the UI. */
  getSession: (id: string) => request<SessionDetail>(`/api/sessions/${id}`),

  deleteSession: (id: string) =>
    request<void>(`/api/sessions/${id}`, { method: "DELETE" }),

  renameSession: (id: string, title: string) =>
    request<SessionSummary>(`/api/sessions/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ title }),
    }),

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
