/**
 * The shared vocabulary between the two halves of the project.
 *
 * Everything the API returns is **derived from the backend's own OpenAPI
 * schema**, not transcribed by hand. This file used to be a manual copy of
 * `backend/app/schemas/chat.py`, which meant a backend rename was caught by
 * nobody until something rendered blank — exactly how `TurnOut.timings` stayed
 * missing from the client for weeks.
 *
 * Regenerate after any backend schema change:
 *
 *     make types        (python backend/scripts/dump_openapi.py && openapi-typescript)
 *
 * CI runs the same command and fails on a diff, so drift cannot be merged.
 *
 * Only types the API does not describe are written by hand below, and each one
 * says why.
 */

import type { components } from "./api-types.gen";

type S = components["schemas"];

// ---- generated, one source of truth --------------------------------------

/**
 * Left exactly as the schema describes them, optional fields included.
 *
 * Tightening these was tempting and wrong: the same profile appears nested
 * inside `TurnOut.acoustic`, where it is the raw shape, so a tightened alias
 * would not be assignable to itself. More importantly, the schema is telling
 * the truth — `events` genuinely has a default and a caller should cope with
 * its absence rather than assume a server behaviour that is not guaranteed.
 * Call sites use `?? []`.
 */
export type DysfluencyEvent = S["DysfluencyEvent"];
export type ProsodyMetrics = S["ProsodyMetrics"];
export type AcousticProfile = S["AcousticProfile"];
export type Citation = S["Citation"];
export type StageTiming = S["StageTiming"];
export type TurnOut = S["TurnOut"];
export type SessionSummary = S["SessionOut"];
export type SessionDetail = S["SessionDetail"];
export type ProgressPoint = S["ProgressPoint"];
export type ProgressOut = S["ProgressOut"];
export type UserOut = S["UserOut"];
export type AuthResponse = S["AuthResponse"];
export type ChatResponse = S["ChatResponse"];

/** The enums arrive as string unions, which is what the UI wants anyway. */
export type Mode = S["Mode"];
export type Role = S["Role"];
export type DysfluencyKind = S["DysfluencyEvent"]["kind"];

// ---- hand-written, and why -----------------------------------------------

/**
 * `GET /api/status` returns `dict[str, Any]`, so OpenAPI describes it as an
 * open object and generation gives us nothing useful. Typed here instead;
 * a contract test asserts the real response still matches these keys.
 */
export interface SystemStatus {
  live_available: boolean;
  llm_reachable: boolean;
  stt_loaded: boolean;
  corpus_chunks: number;
  analyzer: string;
  prompt_version: string;
  llm_variant: string;
}

/**
 * A turn as the UI holds it. Not an API type: it merges what arrived over HTTP
 * with what arrived over the WebSocket, and carries render-only state such as
 * `pending`.
 */
export interface Message {
  id: string;
  role: Role;
  mode: Mode;
  text: string;
  /** Still streaming — rendered with a caret and not yet final. */
  pending?: boolean;
  acoustic?: AcousticProfile | null;
  citations?: Citation[];
  timings?: StageTiming[];
  totalMs?: number;
  grounded?: boolean;
}

/** Human labels. The UI never shows a raw enum value. */
export const EVENT_LABELS: Record<DysfluencyKind, string> = {
  block: "Block",
  prolongation: "Stretched sound",
  sound_repetition: "Repeated sound",
  word_repetition: "Repeated word",
  interjection: "Filler word",
  unsure: "Unclear",
};
