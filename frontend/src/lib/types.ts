/** Mirrors backend/app/schemas — keep the two in step. */

export type DysfluencyKind =
  | "block"
  | "prolongation"
  | "sound_repetition"
  | "word_repetition"
  | "interjection"
  | "unsure";

export interface DysfluencyEvent {
  kind: DysfluencyKind;
  start_ms: number;
  end_ms: number;
  confidence: number;
  duration_ms: number;
}

export interface ProsodyMetrics {
  speech_rate_wpm: number | null;
  articulation_rate_sps: number | null;
  mean_pause_ms: number | null;
  longest_pause_ms: number | null;
  pitch_mean_hz: number | null;
  pitch_variation: number | null;
  energy_variation: number | null;
}

export interface AcousticProfile {
  schema_version: string;
  duration_ms: number;
  events: DysfluencyEvent[];
  prosody: ProsodyMetrics;
  analyzed: boolean;
  source: string;
  event_counts: Record<string, number>;
  dysfluent_ms: number;
  fluency_load: number;
  dominant_event: string | null;
}

export interface Citation {
  source: string;
  title: string | null;
  chunk_index: number | null;
  score: number | null;
  excerpt: string;
}

export type Mode = "live" | "knowledge" | "text";
export type Role = "user" | "coach";

export interface StageTiming {
  stage: string;
  ms: number;
}

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

export interface SystemStatus {
  live_available: boolean;
  llm_reachable: boolean;
  stt_loaded: boolean;
  corpus_chunks: number;
  analyzer: string;
  prompt_version: string;
  llm_variant: string;
}

export interface UserOut {
  id: string;
  email: string;
  display_name: string | null;
  created_at: string;
  memory_enabled: boolean;
}

export interface AuthResponse {
  access_token: string;
  expires_in: number;
  user: UserOut;
}

export interface SessionSummary {
  id: string;
  started_at: string;
  ended_at: string | null;
  turn_count: number;
  title: string | null;
}

/** One persisted turn, as returned by GET /api/sessions/{id}. */
export interface TurnOut {
  id: number;
  role: Role;
  mode: Mode;
  text: string;
  created_at: string;
  citations: Citation[];
  acoustic: AcousticProfile | null;
  total_ms: number | null;
}

export interface SessionDetail extends SessionSummary {
  turns: TurnOut[];
}

export interface ProgressPoint {
  session_id: string;
  started_at: string;
  mean_fluency_load: number;
  mean_speech_rate_wpm: number | null;
  spoken_turns: number;
}

export interface ProgressOut {
  points: ProgressPoint[];
  sessions: number;
  total_practice_ms: number;
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
