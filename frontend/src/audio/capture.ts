/**
 * Microphone capture (A5).
 *
 * Echo cancellation is non-negotiable on the live path: the coach's voice comes
 * out of the speakers while the microphone is still open, and without AEC that
 * audio feeds straight back into Moshi as if the user had said it. Barge-in
 * only works because the browser removes the coach's own voice first.
 *
 * The AudioContext is opened at the server's sample rate so no resampling
 * happens in JavaScript. Browsers honour this for capture in practice, but not
 * universally — `actualSampleRate` reports what was really granted so the
 * caller can tell the server rather than silently sending mis-rated audio.
 */

export interface CaptureOptions {
  sampleRate: number;
  onFrame: (pcm: ArrayBuffer) => void;
  /** Broad amplitude for the level meter. Not used for endpointing. */
  onLevel?: (rms: number) => void;
}

export class MicrophoneCapture {
  private context: AudioContext | null = null;
  private stream: MediaStream | null = null;
  private node: AudioWorkletNode | null = null;
  private source: MediaStreamAudioSourceNode | null = null;
  private analyser: AnalyserNode | null = null;
  private levelTimer: number | null = null;
  // Explicitly ArrayBuffer-backed: TS 5.7+ parameterises the typed arrays, and
  // getFloatTimeDomainData rejects a possibly-SharedArrayBuffer view.
  private levelBuffer: Float32Array<ArrayBuffer> | null = null;

  constructor(private readonly options: CaptureOptions) {}

  get actualSampleRate(): number {
    return this.context?.sampleRate ?? this.options.sampleRate;
  }

  get active(): boolean {
    return this.context !== null;
  }

  async start(): Promise<void> {
    if (this.context) return;

    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    });

    this.context = new AudioContext({ sampleRate: this.options.sampleRate });
    // Chrome starts contexts suspended unless the call is inside a user
    // gesture; resuming explicitly avoids a mic that appears live but is silent.
    if (this.context.state === "suspended") await this.context.resume();

    await this.context.audioWorklet.addModule("/worklets/pcm-capture.js");

    this.source = this.context.createMediaStreamSource(this.stream);
    this.node = new AudioWorkletNode(this.context, "pcm-capture");
    this.node.port.onmessage = (event) => this.options.onFrame(event.data as ArrayBuffer);

    this.source.connect(this.node);
    // Deliberately not connected to destination: routing the microphone to the
    // speakers would let the user hear themselves with a delay, which is
    // actively disruptive for someone working on fluency.

    if (this.options.onLevel) {
      this.analyser = this.context.createAnalyser();
      this.analyser.fftSize = 512;
      this.levelBuffer = new Float32Array(this.analyser.fftSize);
      this.source.connect(this.analyser);
      this.startLevelMeter();
    }
  }

  private startLevelMeter(): void {
    const tick = () => {
      if (!this.analyser || !this.levelBuffer) return;
      this.analyser.getFloatTimeDomainData(this.levelBuffer);
      let sum = 0;
      for (let i = 0; i < this.levelBuffer.length; i++) {
        sum += this.levelBuffer[i] * this.levelBuffer[i];
      }
      this.options.onLevel?.(Math.sqrt(sum / this.levelBuffer.length));
      this.levelTimer = window.setTimeout(tick, 60);
    };
    tick();
  }

  /** Stop sending frames without tearing down the microphone permission. */
  setMuted(muted: boolean): void {
    this.node?.port.postMessage({ type: "mute", value: muted });
  }

  async stop(): Promise<void> {
    if (this.levelTimer !== null) {
      clearTimeout(this.levelTimer);
      this.levelTimer = null;
    }
    this.node?.port.close();
    this.node?.disconnect();
    this.source?.disconnect();
    this.analyser?.disconnect();
    this.stream?.getTracks().forEach((t) => t.stop());
    if (this.context && this.context.state !== "closed") await this.context.close();

    this.node = null;
    this.source = null;
    this.analyser = null;
    this.stream = null;
    this.context = null;
    this.levelBuffer = null;
  }
}
