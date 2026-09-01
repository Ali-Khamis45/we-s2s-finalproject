import { useCallback, useRef } from "react";

/**
 * Attack-fast / release-slow envelope follower.
 *
 * Raw `micLevel` is jittery per frame. Feeding it straight to a radius makes
 * the orb twitch, which is exactly the "motion in the periphery while someone
 * is mid-utterance" the brief rules out — and this app is used by people
 * concentrating hard on speaking.
 *
 * Asymmetric smoothing is what makes it feel like a physical meter rather than
 * a graph: it rises almost immediately so the orb answers your voice, and
 * falls slowly so it settles instead of snapping to zero between syllables.
 *
 * Returns a plain reader/writer pair rather than state — it is driven inside a
 * rAF loop and must never trigger a React render.
 */
export interface Smoother {
  /** Feed the raw value; returns the smoothed one. */
  push(value: number, deltaMs?: number): number;
  /** Current smoothed value without advancing. */
  peek(): number;
  reset(value?: number): void;
}

export function useSmoothed(
  attackMs = 45,
  releaseMs = 420,
  initial = 0,
): Smoother {
  const value = useRef(initial);

  const push = useCallback(
    (target: number, deltaMs = 16.7) => {
      const rising = target > value.current;
      const tau = rising ? attackMs : releaseMs;
      // Frame-rate independent exponential approach, so the feel is identical
      // at 60 Hz and 120 Hz.
      const alpha = tau <= 0 ? 1 : 1 - Math.exp(-deltaMs / tau);
      value.current += (target - value.current) * alpha;
      // Park exactly at zero; a residual 1e-9 keeps the canvas dirty forever.
      if (!rising && value.current < 0.0005) value.current = 0;
      return value.current;
    },
    [attackMs, releaseMs],
  );

  const peek = useCallback(() => value.current, []);
  const reset = useCallback((v = 0) => {
    value.current = v;
  }, []);

  return { push, peek, reset };
}
