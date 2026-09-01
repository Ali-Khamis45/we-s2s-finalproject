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

const LEAD_SECONDS = 0.08;

export class StreamPlayer {
  private context: AudioContext | null = null;
  private gain: GainNode | null = null;
  private nextStartTime = 0;
  private sources = new Set<AudioBufferSourceNode>();

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

    const source = ctx.createBufferSource();
    source.buffer = buffer;
    source.connect(this.gain!);

    // Underrun: the queue drained while we waited for the network. Restart the
    // clock ahead of `currentTime` instead of scheduling in the past, which
    // browsers silently collapse into an immediate, overlapping playback.
    if (this.nextStartTime < ctx.currentTime + LEAD_SECONDS / 2) {
      this.nextStartTime = ctx.currentTime + LEAD_SECONDS;
    }

    source.start(this.nextStartTime);
    this.nextStartTime += buffer.duration;

    this.sources.add(source);
    source.onended = () => this.sources.delete(source);
  }

  /** True while audio is queued or playing — drives the "speaking" indicator. */
  get isPlaying(): boolean {
    if (!this.context) return false;
    return this.sources.size > 0 && this.nextStartTime > this.context.currentTime;
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
  }

  async close(): Promise<void> {
    this.flush();
    if (this.context && this.context.state !== "closed") await this.context.close();
    this.context = null;
    this.gain = null;
    this.nextStartTime = 0;
  }
}
