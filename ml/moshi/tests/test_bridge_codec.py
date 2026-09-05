"""Codec-layer tests for ml/moshi/bridge.py. No GPU or Candle server needed."""
from __future__ import annotations

import math
import struct

from ml.moshi.bridge import OpusOggEncoder

SAMPLE_RATE = 24_000
FRAME_MS = 40
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000  # 960


def tone_pcm(n_frames: int, freq: float = 220.0) -> list[bytes]:
    """n_frames chunks of FRAME_SAMPLES 16-bit mono PCM, a sine tone.

    A tone, not silence: the relay's Opus encoder crashed specifically on
    real (non-silent) audio content, so tests must exercise that case.
    """
    chunks = []
    for i in range(n_frames):
        vals = [
            int(math.sin(2 * math.pi * freq * (i * FRAME_SAMPLES + n) / SAMPLE_RATE) * 8000)
            for n in range(FRAME_SAMPLES)
        ]
        chunks.append(struct.pack(f"<{FRAME_SAMPLES}h", *vals))
    return chunks


def test_encoder_emits_bytes_within_one_frame() -> None:
    """Regression guard: a naive PyAV muxer buffers ~1s before emitting
    anything, which is fatal for real-time streaming (found via spike
    during M2 design). page_duration must make every fed frame flush."""
    enc = OpusOggEncoder()
    produced_immediately = 0
    for chunk in tone_pcm(15):
        out = enc.feed(chunk)
        if out:
            produced_immediately += 1
    enc.close()
    assert produced_immediately == 15
