import { useEffect, useState } from "react";

/**
 * Whether the viewer has asked for reduced motion.
 *
 * The CSS floor in base.css collapses durations, but that is not enough here:
 * the voice orb and the atmosphere run requestAnimationFrame loops, and a
 * collapsed CSS duration does nothing to a canvas. Components read this and
 * **stop their loops entirely** — a paused canvas is the point. A
 * fast-forwarded animation still burns battery and still flickers.
 *
 * Re-evaluates live, because the preference can change while the app is open
 * (a system setting, or a browser devtools emulation during review).
 */
export function useReducedMotionSafe(): boolean {
  const [reduced, setReduced] = useState(() => {
    if (typeof window === "undefined" || !window.matchMedia) return false;
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  });

  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return;
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    const onChange = (e: MediaQueryListEvent) => setReduced(e.matches);

    // Safari below 14 only has the deprecated listener API.
    if (query.addEventListener) {
      query.addEventListener("change", onChange);
      return () => query.removeEventListener("change", onChange);
    }
    query.addListener(onChange);
    return () => query.removeListener(onChange);
  }, []);

  return reduced;
}
