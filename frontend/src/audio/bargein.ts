/**
 * Barge-in detection: has the user started talking over the coach?
 *
 * The UI has always told people "just talk to interrupt", but nothing
 * implemented it — `interrupt()` existed and only the Interrupt button called
 * it. This is the missing half.
 *
 * Deliberately mirrors `backend/app/services/vad.py` rather than inventing a
 * second notion of "is this speech": same adaptive noise floor, same 9 dB
 * margin, same 250 ms minimum. Two different answers to that question on the
 * two sides of one socket is a bug waiting to happen.
 *
 * The hard constraint is echo. The coach's voice is playing out of the
 * speakers while we listen, and browser AEC (`echoCancellation` in capture.ts)
 * is good but not perfect. A detector that trips on leaked playback cancels
 * the coach mid-sentence — worse than no barge-in at all. Hence:
 *
 *   - a sustain requirement, so one leaked syllable is not enough,
 *   - a noise floor that rises slowly and falls fast, so steady echo gets
 *     absorbed into the floor instead of reading as speech,
 *   - and a margin over that floor rather than an absolute level, so it adapts
 *     to the room instead of needing a per-user setting.
 */

/** RMS (0..1 linear) to dBFS. Silence is -Infinity. */
export function rmsToDbfs(rms: number): number {
  return rms > 0 ? 20 * Math.log10(rms) : Number.NEGATIVE_INFINITY;
}

export interface BargeInOptions {
  /** Speech must sit this far above the estimated noise floor. */
  marginDb?: number;
  /** Absolute floor, so a silent room cannot trigger on hiss. */
  floorDbfs?: number;
  /** Sustained speech required before firing. Rejects coughs and clicks. */
  minSpeechMs?: number;
  /** How often `push` is called; capture.ts's level meter ticks at 60 ms. */
  frameMs?: number;
}

export class BargeInDetector {
  private readonly marginDb: number;
  private readonly floorDbfs: number;
  private readonly minSpeechMs: number;
  private readonly frameMs: number;

  private noiseDbfs = -60;
  private history: number[] = [];
  private speechMs = 0;

  constructor(options: BargeInOptions = {}) {
    // Defaults match vad.py's Endpointer field-for-field.
    this.marginDb = options.marginDb ?? 9;
    this.floorDbfs = options.floorDbfs ?? -52;
    this.minSpeechMs = options.minSpeechMs ?? 250;
    this.frameMs = options.frameMs ?? 60;
  }

  get thresholdDbfs(): number {
    return Math.max(this.floorDbfs, this.noiseDbfs + this.marginDb);
  }

  /** Forget accumulated speech, keep the learned noise floor. */
  reset(): void {
    this.speechMs = 0;
  }

  /**
   * Feed one level sample. Returns true exactly once per burst, at the moment
   * sustained speech crosses `minSpeechMs`.
   *
   * `active` is whether the coach is currently speaking. The noise floor keeps
   * updating either way — that is what lets steady echo be learned as noise
   * rather than mistaken for speech the moment playback starts.
   */
  push(rms: number, active: boolean): boolean {
    const level = rmsToDbfs(rms);
    this.updateNoise(level);

    if (!active) {
      // Not barge-in territory; keep calibrating but never fire.
      this.speechMs = 0;
      return false;
    }

    if (level <= this.thresholdDbfs) {
      this.speechMs = 0;
      return false;
    }

    const before = this.speechMs;
    this.speechMs += this.frameMs;
    if (before >= this.minSpeechMs || this.speechMs < this.minSpeechMs) {
      // Fire on the crossing only, so one burst cannot fire repeatedly.
      return false;
    }

    // Crossed the sustain threshold — but continuous playback leaking through
    // AEC also looks like sustained "speech", and firing on it cancels the
    // coach mid-sentence. Real barge-in starts from a quieter moment and rises;
    // steady echo does not. So require the burst to stand out from the recent
    // past, not merely from the slow-moving floor.
    if (this.sustainedSince(this.minSpeechMs)) {
      this.speechMs = 0;
      return false;
    }
    return true;
  }

  /**
   * True when the signal was already above threshold well before this burst —
   * i.e. it never actually started, so it is background, not an interruption.
   */
  private sustainedSince(ms: number): boolean {
    const frames = Math.ceil(ms / this.frameMs);
    // Look back past the burst itself at what preceded it.
    const window = this.history.slice(-(frames * 3), -frames);
    if (window.length < frames) return false;
    const loud = window.filter((l) => l > this.thresholdDbfs).length;
    return loud >= window.length * 0.8;
  }

  private updateNoise(level: number): void {
    if (!Number.isFinite(level)) return;
    this.history.push(level);
    if (this.history.length > 100) this.history.shift();
    if (this.history.length < 8) return;

    const sorted = [...this.history].sort((a, b) => a - b);
    const quiet = sorted.slice(0, Math.max(3, Math.floor(sorted.length / 4)));
    const estimate = quiet.reduce((a, b) => a + b, 0) / quiet.length;

    // Fall fast toward a quieter floor, rise slowly, so one loud frame cannot
    // desensitise the detector for the rest of the turn.
    const weight = estimate < this.noiseDbfs ? 0.25 : 0.05;
    this.noiseDbfs = (1 - weight) * this.noiseDbfs + weight * estimate;
  }
}
