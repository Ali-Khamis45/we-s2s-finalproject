/**
 * Streaming audio playback (A5).
 *
 * Audio arrives as a stream of PCM chunks that must play back seamlessly. The
 * naive approach — call `start()` on each buffer as it lands — produces a click
 * between every chunk, because each one begins at "now" and the gaps compound.
 *
 * Instead, playback time is tracked explicitly and each chunk is scheduled at
 * the exact moment the previous one ends, so the stream is sample-continuous. A
 * small lead cushion absorbs network jitter; when the queue underruns, the
 * clock resets rather than trying to catch up, since a fresh start sounds
 * better than a rush of compressed audio.
 *
 * `flush()` is what makes barge-in audible: when the user talks over the coach,
 * queued audio must stop immediately, not finish the sentence.
 */

/**
 * Cushion before the first chunk of a burst starts playing.
 *
 * 0.08s was sized for network jitter, which is the right instinct for audio
 * arriving faster than real time. It is the wrong size here: the coach's audio
 * is synthesized on CPU at barely better than real time (Kokoro takes ~1.3-2.6s
 * to produce ~2.1-2.5s of speech), so the producer is only just ahead of the
 * consumer. With an 80ms cushion the queue drains during the very first
 * sentence and every chunk boundary becomes an audible gap.
 *
 * A larger head start delays the first word by that much once, then absorbs the
 * per-chunk synthesis jitter for the rest of the reply. Given first audio is
 * already seconds away on this hardware, a fraction of a second spent buying
 * gapless playback is a good trade.
 */
const LEAD_SECONDS = 0.45;

export class StreamPlayer {
  private context: AudioContext | null = null;
  private gain: GainNode | null = null;
  private nextStartTime = 0;
  private sources = new Set<AudioBufferSourceNode>();
  //: Frames held back until LEAD_SECONDS of audio exists to start from.
  private pending: AudioBuffer[] = [];
  private pendingSeconds = 0;
  private startedStream = false;

  constructor(private readonly sampleRate: number) {}

  private ensureContext(): AudioContext {
    if (!this.context) {
      this.context = new AudioContext({ sampleRate: this.sampleRate });
      this.gain = this.context.createGain();
      this.gain.connect(this.context.destination);
    }
    return this.context;
  }

  async resume(): Promise<void> {
    const ctx = this.ensureContext();
    if (ctx.state === "suspended") await ctx.resume();
  }

  /** Queue one chunk of 16-bit PCM. */
  enqueue(pcm: ArrayBuffer): void {
    if (pcm.byteLength < 2) return;

    const ctx = this.ensureContext();
    const ints = new Int16Array(pcm);
    const buffer = ctx.createBuffer(1, ints.length, this.sampleRate);
    const channel = buffer.getChannelData(0);
    for (let i = 0; i < ints.length; i++) channel[i] = ints[i] / 32768;

    // Underrun: the queue drained and playback time has caught up with what is
    // scheduled. Restart the clock ahead of `currentTime` rather than
    // scheduling in the past, which browsers silently collapse into immediate,
    // overlapping playback.
    //
    // The test is against `currentTime`, NOT `currentTime + LEAD/2`. Moshi
    // streams 40ms frames, so on the live path `nextStartTime` is only ever a
    // few tens of ms ahead — comfortably inside any fraction of the lead — and
    // a wider test fired this branch on nearly every frame, re-inserting the
    // full lead as a gap each time. That turned the cushion into the stutter it
    // was meant to prevent.
    if (this.nextStartTime < ctx.currentTime) {
      this.nextStartTime = ctx.currentTime + LEAD_SECONDS;
    }

    // Hold the stream back until a cushion of audio exists, then let it run.
    // Without this the very first frame plays the instant it lands with nothing
    // queued behind it, so on the live path — where frames are 40ms — the
    // decoder is racing the speaker from the first word and every hiccup is
    // audible. Once started, `startedStream` stays true so mid-reply frames are
    // scheduled immediately and gaplessly.
    if (!this.startedStream) {
      this.pendingSeconds += buffer.duration;
      if (this.pendingSeconds < LEAD_SECONDS) {
        this.pending.push(buffer);
        return;
      }
      this.startedStream = true;
      // Start the burst slightly ahead of the clock so the first frame is not
      // scheduled at "now", which leaves the callback no room to run.
      this.nextStartTime = Math.max(this.nextStartTime, ctx.currentTime + 0.02);
      const queued = this.pending;
      this.pending = [];
      for (const b of queued) this.schedule(ctx, b);
    }

    this.schedule(ctx, buffer);
  }

  /** Place one decoded buffer at the end of the scheduled stream. */
  private schedule(ctx: AudioContext, buffer: AudioBuffer): void {
    const source = ctx.createBufferSource();
    source.buffer = buffer;
    source.connect(this.gain!);
    source.start(this.nextStartTime);
    this.nextStartTime += buffer.duration;
    this.sources.add(source);
    source.onended = () => this.sources.delete(source);
  }

  /**
   * True while audio is queued or playing — drives the "speaking" indicator.
   *
   * Deliberately does NOT test `sources.size`. Sources are dropped in their
   * `onended` callback, so between one chunk finishing and the next arriving
   * the set is momentarily empty even though the coach is mid-reply. Gating on
   * it made the "Coach is speaking" hint flicker on and off between sentences,
   * and made barge-in stop watching for exactly that window.
   *
   * The scheduling clock is the honest answer: `nextStartTime` runs ahead of
   * `currentTime` for as long as there is audio still to play.
   */
  get isPlaying(): boolean {
    if (!this.context) return false;
    // Audio held in the prebuffer counts: the coach is speaking from the
    // moment frames start arriving, not from the moment they reach the speaker.
    if (this.pending.length > 0) return true;
    return this.nextStartTime > this.context.currentTime;
  }

  get queuedSeconds(): number {
    if (!this.context) return 0;
    return Math.max(0, this.nextStartTime - this.context.currentTime);
  }

  /** Drop everything queued. This is barge-in. */
  flush(): void {
    for (const source of this.sources) {
      try {
        source.stop();
      } catch {
        // Already ended between the check and the call; nothing to do.
      }
    }
    this.sources.clear();
    this.nextStartTime = this.context?.currentTime ?? 0;
    // Drop the prebuffer too, and re-arm it: after an interruption the next
    // reply is a fresh stream and deserves its own cushion, otherwise it starts
    // racing the speaker exactly as the first one did.
    this.pending = [];
    this.pendingSeconds = 0;
    this.startedStream = false;
  }

  async close(): Promise<void> {
    this.flush();
    if (this.context && this.context.state !== "closed") await this.context.close();
    this.context = null;
    this.gain = null;
    this.nextStartTime = 0;
  }
}
