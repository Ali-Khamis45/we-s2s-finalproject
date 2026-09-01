/**
 * The WebSocket protocol, written down.
 *
 * Both sockets exchanged `{type, data}` objects with the shape existing only in
 * the two handlers that produced and consumed them. A renamed field broke the
 * UI silently, the same failure the generated HTTP types now prevent.
 *
 * This is the single definition. `docs/PROTOCOL.md` documents it in prose with
 * a sequence diagram; the types here are what the code enforces.
 *
 * Binary frames carry raw PCM and are not described here — they have no
 * envelope. Everything else is JSON and is one of the members below.
 */

import type { AcousticProfile, Citation, Mode, Role, StageTiming } from "./types";

// ---- server → client -----------------------------------------------------

/** Knowledge socket only: sent once, immediately after the ticket is accepted. */
export interface ReadyFrame {
  type: "ready";
  data: {
    session_id: string;
    mode: Mode;
    input_sample_rate: number;
    output_sample_rate: number;
  };
}

/** Live socket: which path is active, and why, when it changes. */
export interface ModeFrame {
  type: "mode";
  data: {
    mode: Mode;
    live_available: boolean;
    reason?: string | null;
    detail?: string | null;
    session_id?: string;
    /** Where to reconnect when the live path declined. */
    fallback?: string;
  };
}

/**
 * Incremental or final text.
 *
 * `final: false` is a streaming delta and must be appended; `final: true` is a
 * whole utterance and replaces nothing. Live mode sends only finals for the
 * coach; knowledge mode streams deltas and closes with `done`.
 */
export interface TranscriptFrame {
  type: "transcript";
  data: { role: Role; text: string; final: boolean };
}

/** The acoustic profile of the user's last utterance. */
export interface AcousticFrame {
  type: "acoustic";
  data: AcousticProfile;
}

/** Knowledge socket: what retrieval found, before generation starts. */
export interface CitationsFrame {
  type: "citations";
  data: { grounded: boolean; citations: Citation[] };
}

/** Describes the binary audio frames that follow. */
export interface AudioMetaFrame {
  type: "audio_meta";
  data: {
    sample_rate: number;
    channels?: number;
    format?: "pcm_s16le" | "pcm_f32le";
    /** Set from the speaker's acoustic profile: the coach slows to match. */
    speech_rate: number;
  };
}

/** Live socket: this turn needs material the live path cannot retrieve. */
export interface HandoffFrame {
  type: "handoff";
  data: { reason: string; query: string; endpoint: string; detail?: string };
}

/** Knowledge socket: the turn is complete. */
export interface DoneFrame {
  type: "done";
  data: {
    turn_id: number;
    reply: string;
    grounded: boolean;
    speech_rate?: number;
    timings: StageTiming[];
    total_ms: number;
  };
}

/** Carries the same code vocabulary as the HTTP error envelope. */
export interface ErrorFrame {
  type: "error";
  data: { message: string; code?: string };
}

export type ServerFrame =
  | ReadyFrame
  | ModeFrame
  | TranscriptFrame
  | AcousticFrame
  | CitationsFrame
  | AudioMetaFrame
  | HandoffFrame
  | DoneFrame
  | ErrorFrame;

export type ServerFrameType = ServerFrame["type"];

// ---- client → server -----------------------------------------------------

export type ClientFrame =
  /** Answer what has been buffered now, without waiting for the silence timer. */
  | { type: "flush" }
  /** Close the turn and the socket. */
  | { type: "stop" }
  /** A typed turn over the same socket, so the UI needs one connection. */
  | { type: "text"; data: { message: string } };

// ---- validation ----------------------------------------------------------

const SERVER_FRAME_TYPES: ReadonlySet<string> = new Set<ServerFrameType>([
  "ready",
  "mode",
  "transcript",
  "acoustic",
  "citations",
  "audio_meta",
  "handoff",
  "done",
  "error",
]);

/**
 * Parse an inbound frame, or return null.
 *
 * Returning null rather than throwing is deliberate: a socket carrying live
 * audio must not tear down because one frame was malformed or because the
 * server learned a frame type this client predates. Unknown types are ignored,
 * which makes adding one a backwards-compatible change.
 */
export function parseServerFrame(raw: string): ServerFrame | null {
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    return null;
  }

  if (typeof value !== "object" || value === null) return null;
  const frame = value as { type?: unknown; data?: unknown };

  if (typeof frame.type !== "string" || !SERVER_FRAME_TYPES.has(frame.type)) {
    return null;
  }
  // Every frame carries an object payload; a missing one becomes empty rather
  // than undefined so consumers never guard for it.
  if (frame.data !== undefined && (typeof frame.data !== "object" || frame.data === null)) {
    return null;
  }

  return { type: frame.type, data: frame.data ?? {} } as ServerFrame;
}

// ---- error copy, in exactly one place ------------------------------------

/**
 * Maps a backend error code to what a person reads.
 *
 * One place, so no component invents its own wording and no two screens
 * describe the same failure differently. Codes are the backend's
 * `AppError.code` values; anything unrecognised falls back to the server's own
 * message, which is already written for the user.
 */
export const ERROR_COPY: Record<string, string> = {
  unauthorized: "Sign in to continue.",
  token_expired: "Your session timed out. Signing you back in…",
  bad_credentials: "Those details don't match an account.",
  rate_limited: "Too many attempts. Wait a few minutes and try again.",
  account_locked: "Too many failed attempts. Try again shortly.",
  not_found: "That doesn't exist any more.",
  invalid_request: "Something in that request wasn't valid.",
  model_unavailable:
    "The coaching model isn't running. Start it and try again — everything else still works.",
  dependency_missing: "A required package isn't installed on the server.",
  corpus_empty: "The knowledge base is empty, so answers won't be grounded yet.",
  internal_error: "Something went wrong on our side.",
};

export function errorMessage(code: string | undefined, fallback: string): string {
  return (code && ERROR_COPY[code]) || fallback;
}

/** WebSocket close code for an absent, expired, or reused ticket. */
export const WS_UNAUTHORIZED = 4401;
