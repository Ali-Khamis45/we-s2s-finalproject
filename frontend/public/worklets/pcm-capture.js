/**
 * Microphone capture worklet (A5).
 *
 * Runs on the audio render thread, so it is never blocked by React rendering
 * or garbage collection on the main thread. That matters here more than in a
 * typical recorder: a dropped buffer during a silent block would shorten the
 * block, and the block's duration is precisely what the acoustic branch
 * measures.
 *
 * Emits fixed-size Int16 PCM frames. The AudioContext is created at the
 * sample rate the server wants, so no resampling happens here.
 */

const FRAME_SAMPLES = 480; // 20 ms at 24 kHz, 30 ms at 16 kHz

class PCMCaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this._buffer = new Float32Array(FRAME_SAMPLES);
    this._offset = 0;
    this._muted = false;

    this.port.onmessage = (event) => {
      if (event.data?.type === "mute") this._muted = Boolean(event.data.value);
    };
  }

  process(inputs) {
    const channel = inputs[0]?.[0];
    if (!channel) return true;

    if (this._muted) {
      this._offset = 0;
      return true;
    }

    for (let i = 0; i < channel.length; i++) {
      this._buffer[this._offset++] = channel[i];

      if (this._offset === FRAME_SAMPLES) {
        const pcm = new Int16Array(FRAME_SAMPLES);
        for (let j = 0; j < FRAME_SAMPLES; j++) {
          // Clamp before scaling: values outside [-1, 1] wrap around when cast
          // and turn a loud syllable into a burst of noise.
          const s = Math.max(-1, Math.min(1, this._buffer[j]));
          pcm[j] = s < 0 ? s * 0x8000 : s * 0x7fff;
        }
        // Transferred, not copied — this runs 50 times a second.
        this.port.postMessage(pcm.buffer, [pcm.buffer]);
        this._offset = 0;
      }
    }

    return true;
  }
}

registerProcessor("pcm-capture", PCMCaptureProcessor);
