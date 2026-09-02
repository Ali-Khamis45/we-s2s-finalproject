import { useEffect, useRef, useState } from "react";

import { useReducedMotionSafe } from "../hooks/useReducedMotionSafe";
import { FluidSurface } from "./FluidSurface";
import { useSmoothed } from "../hooks/useSmoothed";

/**
 * The mic control, and the only thing on screen allowed to be fully amber.
 *
 * Four states, each with its own motion — never a spinner, because a spinner
 * says "wait" and none of these mean that:
 *
 *   idle        a slow 4 s breath; the app is listening for you to start
 *   connecting  an arc sweeps the rim; indeterminate but directional
 *   live        radius and glow track micLevel, attack-fast / release-slow
 *   speaking    a steady counter-rotating ring while the coach talks
 *
 * Canvas rather than SVG: the live state redraws a glow and two rings every
 * frame, and doing that through the DOM would thrash layout.
 *
 * Reduced motion stops the loop dead and paints a static ring — but the ring's
 * *radius still encodes mic level*, repainted at most 4×/second. The
 * information survives; only the animation goes.
 */

export type OrbState = "idle" | "connecting" | "live" | "speaking";

interface Props {
  state: OrbState;
  micLevel: number;
  size?: number;
}

const DPR_CAP = 2;

export function VoiceOrb({ state, micLevel, size = 92 }: Props) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const frame = useRef<number | null>(null);
  const lastTime = useRef(0);
  const phase = useRef(0);
  const level = useRef(0);
  const stateRef = useRef<OrbState>(state);
  const reduced = useReducedMotionSafe();
  const smooth = useSmoothed(45, 420, 0);
  // Assume the shader works until it says otherwise, so the common case
  // never flashes the fallback first.
  const [fluid, setFluid] = useState(true);
  const fluidRef = useRef(true);
  // The fluid core replaces the flat fill, so the 2D pass draws only the
  // rings and arcs that carry state.
  const fluidCore = fluid && !reduced && (state === "live" || state === "speaking");
  fluidRef.current = fluidCore;

  level.current = micLevel;
  stateRef.current = state;

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const dpr = Math.min(window.devicePixelRatio || 1, DPR_CAP);
    canvas.width = size * dpr;
    canvas.height = size * dpr;
    ctx.scale(dpr, dpr);

    const styles = getComputedStyle(document.documentElement);
    const amber = styles.getPropertyValue("--orb").trim() || "#f9bf29";
    const amberHot = styles.getPropertyValue("--orb-hot").trim() || "#ffd35c";
    const sage = styles.getPropertyValue("--sage").trim() || "#6f9c8a";
    const rule = styles.getPropertyValue("--rule-firm").trim() || "#33463d";

    const cx = size / 2;
    const cy = size / 2;
    const base = size * 0.26;

    const draw = (amp: number, t: number) => {
      const s = stateRef.current;
      ctx.clearRect(0, 0, size, size);

      const hot = s === "live" || s === "speaking";
      const tint = s === "speaking" ? sage : amber;
      const hotTint = s === "speaking" ? sage : amberHot;

      // Breath: the only movement in idle, slow enough to read as alive
      // rather than as a progress indicator.
      const breath = s === "idle" ? 1 + Math.sin(t / 640) * 0.045 : 1;
      const radius = base * breath * (1 + amp * 0.55);

      // Glow is always on, only its intensity changes. An unlit disc reads as
      // a disabled control; this is a lamp turned low, not off.
      const lit = hot ? 0.42 + amp * 0.5 : s === "connecting" ? 0.3 : 0.2;
      const glow = ctx.createRadialGradient(cx, cy, radius * 0.3, cx, cy, radius * 2.8);
      glow.addColorStop(0, withAlpha(tint, lit * 0.85));
      glow.addColorStop(0.45, withAlpha(tint, lit * 0.3));
      glow.addColorStop(1, withAlpha(tint, 0));
      ctx.fillStyle = glow;
      ctx.beginPath();
      ctx.arc(cx, cy, radius * 2.8, 0, Math.PI * 2);
      ctx.fill();

      // Outer rim: the quiet boundary that is always present.
      ctx.strokeStyle = hot ? withAlpha(tint, 0.5) : rule;
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.arc(cx, cy, size * 0.42, 0, Math.PI * 2);
      ctx.stroke();

      if (s === "connecting") {
        // A sweeping arc, not a spinner ring: it has a head and a tail, so it
        // reads as reaching rather than as waiting.
        const head = (t / 420) % (Math.PI * 2);
        const grad = ctx.createLinearGradient(0, 0, size, size);
        grad.addColorStop(0, withAlpha(amber, 0.15));
        grad.addColorStop(1, withAlpha(amber, 0.95));
        ctx.strokeStyle = grad;
        ctx.lineWidth = 2;
        ctx.lineCap = "round";
        ctx.beginPath();
        ctx.arc(cx, cy, size * 0.42, head, head + Math.PI * 0.55);
        ctx.stroke();
      }

      if (s === "speaking") {
        // Counter-rotating dashes: audibly "the other party is talking".
        ctx.save();
        ctx.translate(cx, cy);
        ctx.rotate(-t / 1600);
        ctx.strokeStyle = withAlpha(sage, 0.75);
        ctx.lineWidth = 2;
        ctx.setLineDash([size * 0.09, size * 0.13]);
        ctx.beginPath();
        ctx.arc(0, 0, size * 0.35, 0, Math.PI * 2);
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.restore();
      }

      // The core, as a lit sphere rather than a flat fill: the highlight sits
      // up and left, so it reads as a light source with a direction.
      // Skipped when the shader is drawing the core beneath this canvas.
      if (!fluidRef.current) {
      const core = ctx.createRadialGradient(
        cx - radius * 0.3,
        cy - radius * 0.34,
        radius * 0.1,
        cx,
        cy,
        radius,
      );
      const solidity = hot ? 1 : 0.72;
      core.addColorStop(0, withAlpha(hotTint, solidity));
      core.addColorStop(0.55, withAlpha(tint, solidity * 0.94));
      core.addColorStop(1, withAlpha(tint, solidity * 0.6));
      ctx.fillStyle = core;
      ctx.beginPath();
      ctx.arc(cx, cy, radius, 0, Math.PI * 2);
      ctx.fill();
      }

      // A live reactive ring just outside the core, so loudness is legible as
      // distance and not only as size.
      if (s === "live" && amp > 0.02) {
        ctx.strokeStyle = withAlpha(amber, 0.28 + amp * 0.5);
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        ctx.arc(cx, cy, radius + 5 + amp * 16, 0, Math.PI * 2);
        ctx.stroke();
      }
    };

    if (reduced) {
      // No loop at all. Repaint on a slow timer so radius still carries level.
      smooth.reset(level.current);
      draw(level.current, 0);
      const id = window.setInterval(() => draw(level.current, 0), 250);
      return () => window.clearInterval(id);
    }

    const tick = (now: number) => {
      const delta = lastTime.current ? now - lastTime.current : 16.7;
      lastTime.current = now;
      phase.current += delta;
      const s = stateRef.current;
      const amp = smooth.push(s === "live" ? level.current : 0, delta);
      draw(amp, phase.current);
      frame.current = requestAnimationFrame(tick);
    };

    frame.current = requestAnimationFrame(tick);
    return () => {
      if (frame.current !== null) cancelAnimationFrame(frame.current);
      frame.current = null;
      lastTime.current = 0;
    };
  }, [reduced, size, smooth]);

  return (
    <span className="orb-stack" style={{ width: size, height: size }}>
      {fluidCore && (
        <FluidSurface
          className="orb-fluid"
          variant="orb"
          intensity={state === "live" ? micLevel : 0.12}
          onSupport={setFluid}
        />
      )}
      <canvas
        ref={canvasRef}
        className="orb"
        style={{ width: size, height: size }}
        aria-hidden="true"
      />
    </span>
  );
}

/** Accepts hex or an already-rgb()/oklch() token and returns it with alpha. */
function withAlpha(color: string, alpha: number): string {
  const a = Math.max(0, Math.min(1, alpha));
  if (color.startsWith("#")) {
    const hex = color.slice(1);
    const full =
      hex.length === 3
        ? hex.split("").map((c) => c + c).join("")
        : hex.padEnd(6, "0").slice(0, 6);
    const n = parseInt(full, 16);
    return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${a})`;
  }
  // color-mix keeps non-hex tokens working without parsing them.
  return `color-mix(in srgb, ${color} ${a * 100}%, transparent)`;
}
