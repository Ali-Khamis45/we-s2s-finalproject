"""Codec-layer tests for ml/moshi/bridge.py. No GPU or Candle server needed."""
from __future__ import annotations

import math
import struct
import time

import pytest

from ml.moshi.bridge import OpusOggDecoder, OpusOggEncoder

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


def test_encode_decode_round_trip_preserves_duration() -> None:
    """Regression guard: Opus always decodes at 48kHz internally regardless
    of the negotiated stream rate (found via spike during M2 design).
    Without an explicit resample to 24kHz, PCM duration comes out 2x wrong."""
    enc = OpusOggEncoder()
    ogg_bytes = bytearray()
    for chunk in tone_pcm(25):  # 25 * 40ms = 1000ms
        ogg_bytes += enc.feed(chunk)
    ogg_bytes += enc.close()

    dec = OpusOggDecoder()
    pcm = bytearray()
    # Feed back in irregular chunk sizes -- not aligned to Ogg pages or
    # plausible websocket message sizes -- to prove chunking doesn't matter.
    # A small sleep between feeds matters here: it lets the decoder's worker
    # thread actually catch up to (and briefly exhaust) the buffered bytes
    # between calls, which is exactly the scenario that broke the
    # synchronous single-container design (see this task's Revision note).
    i = 0
    sizes = [137, 1024, 64, 900, 2048, 300]
    k = 0
    while i < len(ogg_bytes):
        n = sizes[k % len(sizes)]
        dec.feed(bytes(ogg_bytes[i : i + n]))
        pcm += dec.poll(timeout=0.01)
        i += n
        k += 1
        time.sleep(0.003)
    dec.close()
    pcm += dec.poll(timeout=1.0)

    duration_ms = len(pcm) / 2 / SAMPLE_RATE * 1000
    assert 900 <= duration_ms <= 1100, f"expected ~1000ms, got {duration_ms:.0f}ms"


def test_decoder_handles_arbitrary_chunk_boundaries() -> None:
    """The relay's segfault/crash bugs lived in exactly this structural
    case: byte boundaries that don't line up with Ogg page boundaries."""
    enc = OpusOggEncoder()
    ogg_bytes = bytearray()
    for chunk in tone_pcm(10):
        ogg_bytes += enc.feed(chunk)
    ogg_bytes += enc.close()

    dec = OpusOggDecoder()
    total_pcm = bytearray()
    # Single-byte feeds: the most adversarial chunking possible.
    for b in ogg_bytes:
        dec.feed(bytes([b]))
    dec.close()
    total_pcm += dec.poll(timeout=1.0)
    assert len(total_pcm) > 0


def test_decoder_closed_with_zero_bytes_fed_produces_empty_pcm_no_error() -> None:
    """The original bug: closing a decoder that was NEVER fed any bytes
    (e.g. a text-only exchange with no KT_AUDIO) must not raise -- it's a
    benign empty stream, not a decode failure."""
    dec = OpusOggDecoder()
    dec.close()
    pcm = dec.poll(timeout=1.0)
    assert pcm == b""


def test_decoder_closed_after_partial_bytes_reports_error() -> None:
    """Some bytes were fed (a truncated/corrupt Ogg capture) but the probe
    never completed before close() -- this must surface as a real error via
    poll(), not be silently swallowed as if it were an empty stream."""
    dec = OpusOggDecoder()
    # A handful of bytes that look like the start of an Ogg stream but never
    # form a complete, valid page/header -- av.open()'s probe cannot succeed.
    dec.feed(b"OggS")
    dec.close()
    with pytest.raises(Exception):
        dec.poll(timeout=1.0)
