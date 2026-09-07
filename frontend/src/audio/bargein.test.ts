import { describe, expect, it } from "vitest";

import { BargeInDetector, rmsToDbfs } from "./bargein";

/** dBFS -> linear RMS, so tests can speak in the units the design uses. */
const rms = (dbfs: number) => Math.pow(10, dbfs / 20);

/** Feed n frames at one level, returning how many times it fired. */
function feed(d: BargeInDetector, dbfs: number, frames: number, active = true) {
  let fired = 0;
  for (let i = 0; i < frames; i++) if (d.push(rms(dbfs), active)) fired++;
  return fired;
}

describe("rmsToDbfs", () => {
  it("maps silence to -Infinity and full scale to 0", () => {
    expect(rmsToDbfs(0)).toBe(Number.NEGATIVE_INFINITY);
    expect(rmsToDbfs(1)).toBeCloseTo(0);
    expect(rmsToDbfs(0.1)).toBeCloseTo(-20);
  });
});

describe("BargeInDetector", () => {
  it("does not fire while the coach is silent, however loud the room", () => {
    const d = new BargeInDetector();
    // `active: false` — the user talking when no reply is playing is just a
    // normal turn, handled by the server's endpointer, not barge-in.
    expect(feed(d, -10, 50, false)).toBe(0);
  });

  it("fires once after sustained speech over the coach", () => {
    const d = new BargeInDetector({ minSpeechMs: 250, frameMs: 60 });
    feed(d, -70, 20); // establish a quiet noise floor
    // 250ms / 60ms per frame => fires on the 5th loud frame.
    expect(feed(d, -10, 20)).toBe(1);
  });

  it("ignores a cough: loud but shorter than minSpeechMs", () => {
    const d = new BargeInDetector({ minSpeechMs: 250, frameMs: 60 });
    feed(d, -70, 20);
    // Three frames is 180ms, under the 250ms sustain requirement.
    expect(feed(d, -10, 3)).toBe(0);
  });

  it("resets the burst when the level drops back down", () => {
    const d = new BargeInDetector({ minSpeechMs: 250, frameMs: 60 });
    feed(d, -70, 20);
    expect(feed(d, -10, 3)).toBe(0); // 180ms of speech…
    expect(feed(d, -70, 3)).toBe(0); // …interrupted by silence
    expect(feed(d, -10, 3)).toBe(0); // so this restarts, still under 250ms
  });

  it("absorbs steady echo into the noise floor rather than firing on it", () => {
    // The failure mode that matters: leaked playback is continuous, so it
    // should raise the floor and stop reading as speech. A detector that fires
    // here would cancel the coach mid-sentence.
    const d = new BargeInDetector({ minSpeechMs: 250, frameMs: 60 });
    feed(d, -70, 20); // quiet room first

    // Onset is genuinely ambiguous: for the first ~250ms, echo starting and a
    // person starting are the same signal, so at most one fire is allowed
    // there. What must NOT happen is repeated firing for as long as the coach
    // keeps talking — that would cancel every reply a second in.
    const onset = feed(d, -40, 8);
    const sustained = feed(d, -40, 150);

    expect(onset).toBeLessThanOrEqual(1);
    expect(sustained).toBe(0);
  });

  it("still hears real speech above absorbed echo", () => {
    const d = new BargeInDetector({ minSpeechMs: 250, frameMs: 60 });
    feed(d, -70, 20);
    feed(d, -40, 120); // echo learned as the floor
    d.reset();
    // Genuine speech is well above the echo, so it must still get through.
    expect(feed(d, -8, 20)).toBeGreaterThanOrEqual(1);
  });

  it("respects the absolute floor in a silent room", () => {
    // With no noise at all the adaptive floor would sit very low; floorDbfs
    // stops faint hiss from clearing the margin.
    const d = new BargeInDetector({ floorDbfs: -52, minSpeechMs: 250 });
    feed(d, -90, 30);
    expect(d.thresholdDbfs).toBeGreaterThanOrEqual(-52);
    expect(feed(d, -60, 20)).toBe(0);
  });

  it("reset clears the burst but keeps the learned floor", () => {
    const d = new BargeInDetector({ minSpeechMs: 250, frameMs: 60 });
    feed(d, -70, 20);
    const threshold = d.thresholdDbfs;
    feed(d, -10, 3);
    d.reset();
    expect(d.thresholdDbfs).toBeCloseTo(threshold, 5);
    expect(feed(d, -10, 3)).toBe(0); // burst restarted, not carried over
  });
});
