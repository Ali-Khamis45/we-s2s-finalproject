import { beforeEach, describe, expect, it, vi } from "vitest";

import { StreamPlayer } from "./player";

/**
 * The scheduling is the subtle part of streaming playback: each chunk must
 * start exactly where the previous one ended, or a click appears between every
 * buffer. These tests pin that arithmetic with a fake clock.
 */

interface FakeSource {
  buffer: { duration: number } | null;
  startedAt: number | null;
  stopped: boolean;
  onended: (() => void) | null;
  connect: () => void;
  start: (t: number) => void;
  stop: () => void;
}

class FakeContext {
  state = "running";
  currentTime = 0;
  destination = {};
  sources: FakeSource[] = [];
  closed = false;

  constructor(public options: { sampleRate: number }) {}

  async resume() {
    this.state = "running";
  }
  async close() {
    this.closed = true;
    this.state = "closed";
  }
  createGain() {
    return { connect: () => {} };
  }
  createBuffer(_channels: number, length: number, sampleRate: number) {
    return {
      duration: length / sampleRate,
      getChannelData: () => new Float32Array(length),
    };
  }
  createBufferSource(): FakeSource {
    const source: FakeSource = {
      buffer: null,
      startedAt: null,
      stopped: false,
      onended: null,
      connect: () => {},
      start(t: number) {
        this.startedAt = t;
      },
      stop() {
        this.stopped = true;
      },
    };
    this.sources.push(source);
    return source;
  }
}

/** The context the player constructed, or null if it never needed one. */
let ctx: FakeContext | null;

beforeEach(() => {
  ctx = null;
  vi.stubGlobal(
    "AudioContext",
    class extends FakeContext {
      constructor(options: { sampleRate: number }) {
        super(options);
        ctx = this;
      }
    },
  );
});

/** Fails loudly rather than silently reading a stale context from a prior test. */
function audio(): FakeContext {
  if (!ctx) throw new Error("no AudioContext was constructed");
  return ctx;
}

/** One second of 16-bit PCM at 24 kHz. */
function chunk(seconds: number, rate = 24_000): ArrayBuffer {
  return new Int16Array(Math.round(seconds * rate)).buffer;
}

describe("gapless scheduling", () => {
  it("starts each chunk exactly where the previous one ends", () => {
    const player = new StreamPlayer(24_000);

    player.enqueue(chunk(1));
    player.enqueue(chunk(0.5));
    player.enqueue(chunk(0.25));

    const starts = audio().sources.map((s) => s.startedAt!);
    // First lands at the lead cushion; each subsequent one at the previous
    // start plus its predecessor's duration. Any drift here is an audible click.
    expect(starts[1] - starts[0]).toBeCloseTo(1.0, 5);
    expect(starts[2] - starts[1]).toBeCloseTo(0.5, 5);
  });

  it("schedules ahead of the clock rather than at it", () => {
    // Scheduling at exactly currentTime leaves no room for the callback to run
    // and produces dropouts under load.
    const player = new StreamPlayer(24_000);
    player.enqueue(chunk(0.5));
    expect(audio().sources[0].startedAt!).toBeGreaterThan(audio().currentTime);
  });

  it("restarts the clock after an underrun instead of scheduling in the past", () => {
    const player = new StreamPlayer(24_000);
    player.enqueue(chunk(0.5));
    const first = audio().sources[0].startedAt!;

    // The network stalled: playback time has moved well past what was queued.
    audio().currentTime = first + 10;
    player.enqueue(chunk(0.5));

    const second = audio().sources[1].startedAt!;
    // Browsers silently collapse a past start time into an immediate one,
    // overlapping the audio. It must land ahead of the current clock.
    expect(second).toBeGreaterThan(audio().currentTime);
  });

  it("ignores empty and sub-frame payloads without even opening a context", () => {
    // Opening an AudioContext has a real cost and, on some browsers, needs a
    // user gesture. A stray empty frame must not trigger one.
    const player = new StreamPlayer(24_000);
    player.enqueue(new ArrayBuffer(0));
    player.enqueue(new ArrayBuffer(1));
    expect(ctx).toBeNull();
    expect(player.isPlaying).toBe(false);
  });
});

describe("barge-in", () => {
  it("stops everything queued when flushed", () => {
    // This is what makes interrupting the coach work: queued audio must stop
    // immediately, not finish the sentence.
    const player = new StreamPlayer(24_000);
    player.enqueue(chunk(1));
    player.enqueue(chunk(1));

    player.flush();

    expect(audio().sources.every((s) => s.stopped)).toBe(true);
    expect(player.isPlaying).toBe(false);
    expect(player.queuedSeconds).toBe(0);
  });

  it("survives a source that already ended", () => {
    const player = new StreamPlayer(24_000);
    player.enqueue(chunk(1));
    audio().sources[0].stop = () => {
      throw new Error("InvalidStateError");
    };
    expect(() => player.flush()).not.toThrow();
  });

  it("resumes cleanly after a flush", () => {
    const player = new StreamPlayer(24_000);
    player.enqueue(chunk(1));
    player.flush();
    audio().currentTime = 5;
    player.enqueue(chunk(0.5));

    const latest = audio().sources[audio().sources.length - 1];
    expect(latest.startedAt!).toBeGreaterThan(5);
  });
});

describe("queue reporting", () => {
  it("reports buffered audio ahead of the clock", () => {
    const player = new StreamPlayer(24_000);
    player.enqueue(chunk(2));
    expect(player.queuedSeconds).toBeGreaterThan(1.9);
    expect(player.isPlaying).toBe(true);
  });

  it("reports nothing playing before any audio arrives", () => {
    const player = new StreamPlayer(24_000);
    expect(player.isPlaying).toBe(false);
    expect(player.queuedSeconds).toBe(0);
  });
});

