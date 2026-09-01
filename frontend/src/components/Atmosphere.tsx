import { useEffect, useRef } from "react";

import { useReducedMotionSafe } from "../hooks/useReducedMotionSafe";
import { useSmoothed } from "../hooks/useSmoothed";

/**
 * The room the app sits in: a warm light-fall, grain, and a vignette.
 *
 * Entirely procedural. No image ships — a static file cannot respond to the
 * voice in the room, and this one does: `--lamp-warmth` rises while the mic is
 * live so the lamp warms and opens, then falls back when it goes quiet.
 *
 * Heavily smoothed on a long release, because peripheral movement is a
 * functional problem for someone concentrating on speaking. It should register
 * as the room being a little warmer, not as something moving.
 */
interface Props {
  micLevel: number;
  listening: boolean;
  connected: boolean;
}

const IDLE_WARMTH = 0.3;
const LIVE_CEILING = 1;

export function Atmosphere({ micLevel, listening, connected }: Props) {
  const rootRef = useRef<HTMLDivElement | null>(null);
  const frame = useRef<number | null>(null);
  const lastTime = useRef(0);
  const target = useRef(IDLE_WARMTH);
  const reduced = useReducedMotionSafe();
  // Very long release: the room cools slowly, it does not flicker off.
  const warmth = useSmoothed(600, 2200, IDLE_WARMTH);

  target.current = listening
    ? IDLE_WARMTH + (LIVE_CEILING - IDLE_WARMTH) * Math.min(1, micLevel * 3)
    : IDLE_WARMTH;

  useEffect(() => {
    const el = rootRef.current;
    if (!el) return;

    // Reduced motion: paint once at a settled value and never start a loop.
    if (reduced) {
      warmth.reset(IDLE_WARMTH);
      el.style.setProperty("--lamp-warmth", String(IDLE_WARMTH));
      return;
    }

    const tick = (now: number) => {
      const delta = lastTime.current ? now - lastTime.current : 16.7;
      lastTime.current = now;
      const next = warmth.push(target.current, delta);
      // Two decimals is below the perceptual floor for a gradient this soft,
      // and it stops the style write invalidating on every frame.
      el.style.setProperty("--lamp-warmth", next.toFixed(2));
      frame.current = requestAnimationFrame(tick);
    };

    frame.current = requestAnimationFrame(tick);
    return () => {
      if (frame.current !== null) cancelAnimationFrame(frame.current);
      frame.current = null;
      lastTime.current = 0;
    };
  }, [reduced, warmth]);

  return (
    <div
      ref={rootRef}
      className="atmosphere"
      data-session={connected ? "connected" : "idle"}
      aria-hidden="true"
    >
      <div className="atmo-lamp" />
      <svg className="atmo-grain" xmlns="http://www.w3.org/2000/svg">
        <filter id="atmo-noise">
          {/* Fine, high-frequency noise. Coarser turbulence reads as texture; * this reads as film. */}
          <feTurbulence
            type="fractalNoise"
            baseFrequency="0.82"
            numOctaves="3"
            stitchTiles="stitch"
          />
          <feColorMatrix type="saturate" values="0" />
        </filter>
        <rect width="100%" height="100%" filter="url(#atmo-noise)" />
      </svg>
      <div className="atmo-vignette" />
    </div>
  );
}
