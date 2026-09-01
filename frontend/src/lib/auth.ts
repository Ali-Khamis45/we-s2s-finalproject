import type { UserOut } from "./types";

/**
 * The access token, and how it is renewed.
 *
 * It lives in a module-scope variable and nowhere else. Not `localStorage`, not
 * `sessionStorage`: anything in web storage is readable by any script that ever
 * gets injected into the page, and this token opens someone's speech
 * transcripts. Held in memory it dies with the tab, which is the entire reason
 * a bearer token is defensible here at all.
 *
 * What survives a reload is the refresh cookie, which is HttpOnly and which
 * JavaScript therefore cannot read — including malicious JavaScript.
 */

let accessToken: string | null = null;
let currentUser: UserOut | null = null;

type Listener = (user: UserOut | null) => void;
const listeners = new Set<Listener>();

export function getAccessToken(): string | null {
  return accessToken;
}

export function getUser(): UserOut | null {
  return currentUser;
}

export function setAuth(token: string | null, user: UserOut | null): void {
  accessToken = token;
  currentUser = user;
  listeners.forEach((fn) => fn(user));
}

export function clearAuth(): void {
  setAuth(null, null);
}

export function subscribe(fn: Listener): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function authHeader(): Record<string, string> {
  return accessToken ? { Authorization: `Bearer ${accessToken}` } : {};
}

/**
 * Single-flight refresh.
 *
 * Ten requests can 401 at once when a token expires mid-page. Without this,
 * each would fire its own refresh, and because refresh *rotates*, nine of them
 * would present an already-used token — which the server correctly reads as a
 * stolen-token replay and responds to by revoking the entire family. The user
 * would be signed out by their own app.
 */
let inFlight: Promise<boolean> | null = null;

export function refreshAccessToken(): Promise<boolean> {
  if (inFlight) return inFlight;

  inFlight = (async () => {
    try {
      const r = await fetch("/api/auth/refresh", {
        method: "POST",
        credentials: "include",
      });
      if (!r.ok) {
        clearAuth();
        return false;
      }
      const body = await r.json();
      setAuth(body.access_token, body.user);
      return true;
    } catch {
      clearAuth();
      return false;
    } finally {
      // Cleared in a microtask so concurrent callers all observe this attempt
      // rather than starting a second one.
      queueMicrotask(() => {
        inFlight = null;
      });
    }
  })();

  return inFlight;
}

/** A fresh, single-use ticket for opening a WebSocket. Never cached. */
export async function fetchWsTicket(): Promise<string | null> {
  try {
    const r = await fetch("/api/auth/ws-ticket", {
      method: "POST",
      headers: authHeader(),
      credentials: "include",
    });
    if (r.status === 401 && (await refreshAccessToken())) {
      return fetchWsTicket();
    }
    if (!r.ok) return null;
    return (await r.json()).ticket as string;
  } catch {
    return null;
  }
}
