/**
 * Playing one stored coach reply.
 *
 * Deliberately not part of `StreamPlayer`. That class schedules live PCM
 * chunks so they butt up sample-continuously against each other, and it has no
 * notion of a clip that ends. A replay is one finished buffer that has to stop
 * on demand and report when it finished, which is a different job.
 *
 * The module owns the "only one voice at a time" rule, because that rule has to
 * hold across components: two speaker buttons on two turns must not be able to
 * talk over each other, and neither must talk over the live coach.
 */

import { api } from "../lib/api";

let context: AudioContext | null = null;
let current: AudioBufferSourceNode | null = null;

/** Lazily created: constructing one before a gesture leaves it suspended. */
function ensureContext(): AudioContext {
  context ??= new AudioContext();
  return context;
}

/** Stop whatever is playing. Safe to call when nothing is. */
export function stop(): void {
  if (!current) return;
  const node = current;
  current = null;
  // onended fires on an explicit stop too; clearing first keeps the handler
  // from cancelling a replay that has already been started in its place.
  node.onended = null;
  try {
    node.stop();
  } catch {
    // Already finished. Nothing to do.
  }
  node.disconnect();
}

interface PlayOptions {
  /** Called when sound actually begins, which is not when the press happened. */
  onStart?: () => void;
}

/**
 * Fetch and play one coach turn. Resolves when playback finishes, and also
 * when it is cut short, so callers can clear their playing state either way.
 */
export async function play(
  sessionId: string,
  turnId: number,
  { onStart }: PlayOptions = {},
): Promise<void> {
  // Stop before the request, not after it: pressing a second button should go
  // quiet immediately rather than after a synthesis round trip.
  stop();

  const bytes = await api.turnSpeech(sessionId, turnId);
  const ctx = ensureContext();
  // Some browsers suspend the context until a gesture has reached it.
  if (ctx.state === "suspended") await ctx.resume();

  const buffer = await ctx.decodeAudioData(bytes);

  return new Promise<void>((resolve) => {
    const source = ctx.createBufferSource();
    source.buffer = buffer;
    source.connect(ctx.destination);
    source.onended = () => {
      if (current === source) current = null;
      resolve();
    };
    current = source;
    source.start();
    onStart?.();
  });
}

/** Whether a replay is sounding right now. */
export function isPlaying(): boolean {
  return current !== null;
}
