import { useEffect, useRef } from "react";

import { useReducedMotionSafe } from "../hooks/useReducedMotionSafe";

/**
 * A liquid surface, in one fragment shader.
 *
 * Domain-warped fractal noise: the field is sampled, that sample displaces the
 * coordinates, and it is sampled again. Warping the domain rather than the
 * output is what makes it read as fluid instead of as moving static.
 *
 * It has two homes here, and the second is why it exists at all:
 *
 *   variant="panel"  the landing hero, driven by the pointer
 *   variant="orb"    the microphone control, driven by `intensity`, which the
 *                    caller feeds from micLevel
 *
 * In the orb it stops being a hover effect. The surface is still when the room
 * is quiet and turns liquid when someone speaks, which makes it a picture of
 * the audio signal a transcript throws away — the thing this project is about.
 *
 * Plain WebGL, no three.js: this is one triangle and a shader. `onSupport`
 * reports false when a context or a compile fails, so the caller can render a
 * real fallback rather than a blank rectangle.
 */

const VERT = `
attribute vec2 aPos;
varying vec2 vUv;
void main() {
  vUv = aPos * 0.5 + 0.5;
  gl_Position = vec4(aPos, 0.0, 1.0);
}
`;

const FRAG = `
precision mediump float;

varying vec2 vUv;
uniform float uTime;
uniform vec2  uPointer;
uniform float uPointerStrength;
uniform float uIntensity;
uniform vec3  uColorA;
uniform vec3  uColorB;
uniform float uCircular;
uniform float uAspect;

vec3 permute(vec3 x) { return mod(((x * 34.0) + 1.0) * x, 289.0); }

float snoise(vec2 v) {
  const vec4 C = vec4(0.211324865405187, 0.366025403784439,
                     -0.577350269189626, 0.024390243902439);
  vec2 i  = floor(v + dot(v, C.yy));
  vec2 x0 = v - i + dot(i, C.xx);
  vec2 i1 = (x0.x > x0.y) ? vec2(1.0, 0.0) : vec2(0.0, 1.0);
  vec4 x12 = x0.xyxy + C.xxzz;
  x12.xy -= i1;
  i = mod(i, 289.0);
  vec3 p = permute(permute(i.y + vec3(0.0, i1.y, 1.0))
                          + i.x + vec3(0.0, i1.x, 1.0));
  vec3 m = max(0.5 - vec3(dot(x0, x0), dot(x12.xy, x12.xy),
                          dot(x12.zw, x12.zw)), 0.0);
  m = m * m; m = m * m;
  vec3 x = 2.0 * fract(p * C.www) - 1.0;
  vec3 h = abs(x) - 0.5;
  vec3 ox = floor(x + 0.5);
  vec3 a0 = x - ox;
  m *= 1.79284291400159 - 0.85373472095314 * (a0 * a0 + h * h);
  vec3 g;
  g.x  = a0.x  * x0.x  + h.x  * x0.y;
  g.yz = a0.yz * x12.xz + h.yz * x12.yw;
  return 130.0 * dot(m, g);
}

float fbm(vec2 p) {
  float total = 0.0;
  float amp = 0.5;
  for (int i = 0; i < 3; i++) {
    total += snoise(p) * amp;
    p *= 2.02;
    amp *= 0.5;
  }
  return total;
}

void main() {
  vec2 p = vUv - 0.5;
  p.x *= uAspect;

  float t = uTime * (0.06 + uIntensity * 0.22);

  vec2 toPointer = p - uPointer;
  float push = uPointerStrength * exp(-dot(toPointer, toPointer) * 9.0);

  vec2 q = vec2(fbm(p * 1.6 + t), fbm(p * 1.6 + vec2(3.1, 1.7) - t));
  float warp = 0.7 + uIntensity * 1.5;
  vec2 r = vec2(
    fbm(p * 2.1 + q * warp + vec2(1.7, 9.2) + t * 0.7),
    fbm(p * 2.1 + q * warp + vec2(8.3, 2.8) - t * 0.6)
  );
  float field = fbm(p * 1.9 + r * (0.9 + push * 2.0));

  float mixAmount = clamp(field * 0.5 + 0.5, 0.0, 1.0);
  mixAmount = pow(mixAmount, 1.6 - uIntensity * 0.6);
  vec3 color = mix(uColorA, uColorB, mixAmount);

  color += uColorB * uIntensity * 0.25 * smoothstep(0.5, 0.0, length(p));

  float alpha = 1.0;
  if (uCircular > 0.5) {
    alpha = 1.0 - smoothstep(0.42, 0.5, length(p));
  }

  gl_FragColor = vec4(color, alpha);
}
`;

export interface FluidSurfaceProps {
  /** 0..1. In the orb this is smoothed mic level; in the hero it is fixed. */
  intensity: number;
  variant?: "orb" | "panel";
  /** Pointer reactivity. Off in the practice room by design. */
  followPointer?: boolean;
  className?: string;
  /** Called with false when WebGL is unavailable, so the caller can fall back. */
  onSupport?: (supported: boolean) => void;
}

type RGB = [number, number, number];

function readToken(name: string, fallback: RGB): RGB {
  const raw = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const hex = raw.startsWith("#") ? raw.slice(1) : "";
  if (hex.length !== 6) return fallback;
  const n = Number.parseInt(hex, 16);
  return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
}

export function FluidSurface({
  intensity,
  variant = "orb",
  followPointer = false,
  className,
  onSupport,
}: FluidSurfaceProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const intensityRef = useRef(intensity);
  const supportRef = useRef(onSupport);
  const reduced = useReducedMotionSafe();

  intensityRef.current = intensity;
  supportRef.current = onSupport;

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const report = (ok: boolean) => supportRef.current?.(ok);

    const gl = canvas.getContext("webgl", {
      alpha: true,
      antialias: false,
      premultipliedAlpha: false,
    });
    if (!gl) {
      report(false);
      return;
    }

    const compile = (type: number, source: string) => {
      const shader = gl.createShader(type);
      if (!shader) return null;
      gl.shaderSource(shader, source);
      gl.compileShader(shader);
      if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
        // A driver that refuses the shader is the same situation as no WebGL,
        // so it takes the same path.
        gl.deleteShader(shader);
        return null;
      }
      return shader;
    };

    const vs = compile(gl.VERTEX_SHADER, VERT);
    const fs = compile(gl.FRAGMENT_SHADER, FRAG);
    const program = vs && fs ? gl.createProgram() : null;
    if (!vs || !fs || !program) {
      report(false);
      return;
    }

    gl.attachShader(program, vs);
    gl.attachShader(program, fs);
    gl.linkProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
      report(false);
      return;
    }
    gl.useProgram(program);
    report(true);

    // One triangle covering the viewport: cheaper than a quad, and no diagonal
    // seam where two triangles meet.
    const buffer = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
    const aPos = gl.getAttribLocation(program, "aPos");
    gl.enableVertexAttribArray(aPos);
    gl.vertexAttribPointer(aPos, 2, gl.FLOAT, false, 0, 0);

    const u = {
      time: gl.getUniformLocation(program, "uTime"),
      pointer: gl.getUniformLocation(program, "uPointer"),
      pointerStrength: gl.getUniformLocation(program, "uPointerStrength"),
      intensity: gl.getUniformLocation(program, "uIntensity"),
      colorA: gl.getUniformLocation(program, "uColorA"),
      colorB: gl.getUniformLocation(program, "uColorB"),
      circular: gl.getUniformLocation(program, "uCircular"),
      aspect: gl.getUniformLocation(program, "uAspect"),
    };

    gl.uniform3fv(u.colorA, readToken("--ground-2", [0.078, 0.114, 0.098]));
    gl.uniform3fv(
      u.colorB,
      readToken(variant === "orb" ? "--orb" : "--amber", [0.976, 0.749, 0.161]),
    );
    gl.uniform1f(u.circular, variant === "orb" ? 1 : 0);

    gl.enable(gl.BLEND);
    gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);

    const resize = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const w = Math.max(1, Math.round(canvas.clientWidth * dpr));
      const h = Math.max(1, Math.round(canvas.clientHeight * dpr));
      if (canvas.width !== w || canvas.height !== h) {
        canvas.width = w;
        canvas.height = h;
        gl.viewport(0, 0, w, h);
        gl.uniform1f(u.aspect, w / h);
      }
    };
    resize();

    const pointer = { x: 0, y: 0, strength: 0 };
    const onPointerMove = (e: PointerEvent) => {
      const rect = canvas.getBoundingClientRect();
      if (!rect.width || !rect.height) return;
      pointer.x = ((e.clientX - rect.left) / rect.width - 0.5) * (rect.width / rect.height);
      pointer.y = 0.5 - (e.clientY - rect.top) / rect.height;
      pointer.strength = 1;
    };
    if (followPointer) {
      window.addEventListener("pointermove", onPointerMove, { passive: true });
    }

    const draw = (elapsed: number) => {
      resize();
      gl.uniform1f(u.time, elapsed);
      gl.uniform2f(u.pointer, pointer.x, pointer.y);
      gl.uniform1f(u.pointerStrength, pointer.strength);
      gl.uniform1f(u.intensity, Math.min(1, Math.max(0, intensityRef.current)));
      gl.drawArrays(gl.TRIANGLES, 0, 3);
    };

    let frame: number | null = null;
    let visible = true;
    const start = performance.now();

    const loop = (now: number) => {
      // Decays rather than resetting, so the surface settles after the cursor
      // leaves instead of snapping flat.
      pointer.strength *= 0.94;
      draw((now - start) / 1000);
      frame = visible ? requestAnimationFrame(loop) : null;
    };

    if (reduced) {
      // Exactly one frame, then nothing. A paused surface is the point; a
      // fast-forwarded one still burns battery and still flickers.
      draw(0);
    } else {
      frame = requestAnimationFrame(loop);
    }

    // Stop rendering entirely when scrolled out of view.
    const observer = new IntersectionObserver(
      (entries) => {
        visible = entries.some((e) => e.isIntersecting);
        if (visible && !reduced && frame === null) frame = requestAnimationFrame(loop);
      },
      { threshold: 0 },
    );
    observer.observe(canvas);

    const onLost = (e: Event) => {
      e.preventDefault();
      if (frame !== null) cancelAnimationFrame(frame);
      frame = null;
      report(false);
    };
    canvas.addEventListener("webglcontextlost", onLost);

    return () => {
      if (frame !== null) cancelAnimationFrame(frame);
      observer.disconnect();
      canvas.removeEventListener("webglcontextlost", onLost);
      if (followPointer) window.removeEventListener("pointermove", onPointerMove);
      gl.deleteBuffer(buffer);
      gl.deleteProgram(program);
      gl.deleteShader(vs);
      gl.deleteShader(fs);
    };
  }, [followPointer, reduced, variant]);

  return <canvas ref={canvasRef} className={className} aria-hidden="true" />;
}
