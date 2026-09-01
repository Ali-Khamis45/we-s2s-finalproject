"""Audio plumbing shared by the STT, TTS, and analyzer services.

The two paths run at different rates: the browser and Moshi speak 24 kHz, while
Whisper and wav2vec2 want 16 kHz. Conversion happens here so no service has to
care what fed it.

numpy is a hard dependency; scipy and librosa are not, so resampling degrades to
a linear interpolation that is fine for speech at these ratios.
"""

from __future__ import annotations

import io
import wave

import numpy as np

INT16_SCALE = 32768.0


def pcm16_to_float32(data: bytes) -> np.ndarray:
    """Decode little-endian signed 16-bit PCM into [-1.0, 1.0] float32."""
    if not data:
        return np.zeros(0, dtype=np.float32)
    # An odd trailing byte means a frame was split across reads; drop it rather
    # than raising, because on a live socket the next chunk carries the rest.
    if len(data) % 2:
        data = data[:-1]
    return (np.frombuffer(data, dtype="<i2").astype(np.float32) / INT16_SCALE).copy()


def float32_to_pcm16(samples: np.ndarray) -> bytes:
    """Encode float32 audio as little-endian signed 16-bit PCM, with clipping."""
    if samples.size == 0:
        return b""
    clipped = np.clip(samples, -1.0, 1.0)
    return (clipped * (INT16_SCALE - 1)).astype("<i2").tobytes()


def resample(samples: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    """Resample mono float32 audio."""
    if src_rate == dst_rate or samples.size == 0:
        return samples.astype(np.float32, copy=False)

    try:
        from scipy.signal import resample_poly

        from math import gcd

        g = gcd(src_rate, dst_rate)
        return resample_poly(samples, dst_rate // g, src_rate // g).astype(np.float32)
    except ImportError:
        n_out = int(round(samples.size * dst_rate / src_rate))
        if n_out <= 0:
            return np.zeros(0, dtype=np.float32)
        src_idx = np.linspace(0, samples.size - 1, num=n_out, dtype=np.float64)
        return np.interp(src_idx, np.arange(samples.size), samples).astype(np.float32)


def to_mono(samples: np.ndarray, channels: int) -> np.ndarray:
    if channels <= 1 or samples.size == 0:
        return samples
    usable = samples.size - (samples.size % channels)
    return samples[:usable].reshape(-1, channels).mean(axis=1).astype(np.float32)


def duration_ms(samples: np.ndarray, sample_rate: int) -> int:
    if sample_rate <= 0:
        return 0
    return int(round(samples.size / sample_rate * 1000))


def rms_dbfs(samples: np.ndarray) -> float:
    """Loudness in dBFS. Silence returns -inf, which compares correctly."""
    if samples.size == 0:
        return float("-inf")
    rms = float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))
    if rms <= 1e-9:
        return float("-inf")
    return 20.0 * float(np.log10(rms))


def wav_bytes(samples: np.ndarray, sample_rate: int) -> bytes:
    """Wrap float32 audio in a WAV container, for debugging and demo capture."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(float32_to_pcm16(samples))
    return buf.getvalue()


def read_wav(data: bytes) -> tuple[np.ndarray, int]:
    """Decode a WAV upload into mono float32 plus its sample rate."""
    with wave.open(io.BytesIO(data), "rb") as wf:
        channels = wf.getnchannels()
        width = wf.getsampwidth()
        rate = wf.getframerate()
        frames = wf.readframes(wf.getnframes())

    if width == 2:
        samples = pcm16_to_float32(frames)
    elif width == 4:
        samples = np.frombuffer(frames, dtype="<f4").astype(np.float32).copy()
    elif width == 1:
        samples = (np.frombuffer(frames, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    else:
        raise ValueError(f"Unsupported WAV sample width: {width} bytes")

    return to_mono(samples, channels), rate


class StreamBuffer:
    """Accumulates incoming PCM frames until an utterance is complete.

    A WebSocket delivers audio in ~20 ms chunks; both the transcriber and the
    analyzer want the whole utterance. This holds the tail and hands it over on
    demand.
    """

    def __init__(self, sample_rate: int, max_seconds: float = 45.0) -> None:
        self.sample_rate = sample_rate
        self._max = int(sample_rate * max_seconds)
        self._chunks: list[np.ndarray] = []
        self._size = 0

    def add_pcm16(self, data: bytes) -> None:
        self.add(pcm16_to_float32(data))

    def add(self, samples: np.ndarray) -> None:
        if samples.size == 0:
            return
        self._chunks.append(samples)
        self._size += samples.size
        # Cap the buffer so a stuck endpoint detector cannot grow it unbounded.
        while self._size > self._max and len(self._chunks) > 1:
            self._size -= self._chunks.pop(0).size

    def collect(self) -> np.ndarray:
        if not self._chunks:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(self._chunks).astype(np.float32, copy=False)

    def clear(self) -> None:
        self._chunks.clear()
        self._size = 0

    def take(self) -> np.ndarray:
        out = self.collect()
        self.clear()
        return out

    @property
    def duration_ms(self) -> int:
        return int(round(self._size / self.sample_rate * 1000)) if self.sample_rate else 0

    def __len__(self) -> int:
        return self._size
