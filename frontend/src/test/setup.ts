import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

// jsdom implements neither of these, and both are used by the conversation view
// and the streaming player.
Element.prototype.scrollIntoView = vi.fn();

if (!("AudioContext" in globalThis)) {
  // Minimal stand-in so modules that construct one at import time do not throw.
  // Behaviour is asserted in the player's own tests with an explicit fake.
  (globalThis as unknown as { AudioContext: unknown }).AudioContext = class {
    state = "running";
    currentTime = 0;
    destination = {};
    async resume() {}
    async close() {}
    createGain() {
      return { connect() {}, gain: { value: 1 } };
    }
    createBuffer(_c: number, length: number) {
      return { duration: length / 24000, getChannelData: () => new Float32Array(length) };
    }
    createBufferSource() {
      return {
        buffer: null,
        connect() {},
        start() {},
        stop() {},
        onended: null,
      };
    }
  };
}
