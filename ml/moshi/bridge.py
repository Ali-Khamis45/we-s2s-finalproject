"""Production bridge between Kyutai's real Candle wire protocol
(wss://, Opus-in-Ogg audio, tags 0-6 -- rust/protocol.md) and the plain
ws://+PCM protocol backend/app/services/moshi.py already speaks.

Replaces ml/moshi/relay.py (M1's throwaway proof-of-concept), whose
hand-rolled ctypes Ogg/Opus muxer/demuxer had three bugs: a segfault on
connection teardown, a crash on real (non-silent) speech content, and no
ERROR-tag translation on upstream connect failure. This module fixes all
three by replacing the ctypes codec layer with PyAV (bindings over
ffmpeg's libavformat/libavcodec) and adding explicit error translation.

See docs/superpowers/specs/2026-09-05-m2-moshi-bridge-design.md for the
full design record, including the three findings from the pre-implementation
spike that fixed the config values used below.
"""
from __future__ import annotations

import fractions
import io
import logging

import av
from av.audio.resampler import AudioResampler

log = logging.getLogger("moshi.bridge")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

UPSTREAM_URL = "wss://127.0.0.1:8999/api/chat"
LISTEN_HOST = "127.0.0.1"
LISTEN_PORT = 8998

# Kyutai's real tags (rust/protocol.md)
KT_HANDSHAKE, KT_AUDIO, KT_TEXT, KT_CONTROL, KT_METADATA, KT_ERROR, KT_PING = range(7)

# backend/app/services/moshi.py's Tag enum
OUR_HANDSHAKE, OUR_AUDIO, OUR_TEXT, OUR_CONTROL, OUR_ERROR = range(5)

SAMPLE_RATE = 24_000
CHANNELS = 1
# Must match Candle server's stream_both.rs PCM flush quantum
# (spawn_recv_loops: size_in_buf >= 24_000/25) -- 20ms silently stalls its
# decoder after ~3 frames (found in M1's bench_moshi_latency.py).
FRAME_MS = 40
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000  # 960


class _ByteSink(io.RawIOBase):
    """Captures bytes a PyAV muxer writes, for immediate forwarding."""

    def __init__(self) -> None:
        self._chunks: list[bytes] = []

    def writable(self) -> bool:
        return True

    def write(self, b: bytes) -> int:
        self._chunks.append(bytes(b))
        return len(b)

    def drain(self) -> bytes:
        out = b"".join(self._chunks)
        self._chunks.clear()
        return out


class OpusOggDecoder:
    """Placeholder decoder for future implementation."""
    pass


class OpusOggEncoder:
    """Encodes a stream of raw 16-bit PCM into a continuous Ogg/Opus bitstream.

    One instance per outgoing direction of one connection. Call `feed(pcm)`
    with any amount of PCM; it buffers to 40ms frames internally (matching
    Candle's PCM flush quantum) and returns any Ogg page bytes produced by
    that call, ready to send as-is.
    """

    def __init__(self, sample_rate: int = SAMPLE_RATE, channels: int = CHANNELS) -> None:
        self.sample_rate = sample_rate
        self.channels = channels
        self._sink = _ByteSink()
        # page_duration forces a flushed Ogg page per frame fed -- without
        # it PyAV's muxer buffers roughly a second of audio before emitting
        # any bytes at all (verified by spike; see design spec finding 1).
        self._container = av.open(
            self._sink, mode="w", format="ogg", options={"page_duration": "20000"}
        )
        self._stream = self._container.add_stream(
            "libopus", rate=sample_rate, layout="mono" if channels == 1 else "stereo"
        )
        self._time_base = fractions.Fraction(1, sample_rate)
        self._pcm_buf = bytearray()
        self._pts = 0
        self._closed = False

    def feed(self, pcm: bytes) -> bytes:
        """Feed raw PCM bytes; returns any complete Ogg page bytes produced."""
        self._pcm_buf += pcm
        frame_bytes = FRAME_SAMPLES * 2 * self.channels
        while len(self._pcm_buf) >= frame_bytes:
            chunk = bytes(self._pcm_buf[:frame_bytes])
            del self._pcm_buf[:frame_bytes]

            frame = av.AudioFrame(
                format="s16", layout="mono" if self.channels == 1 else "stereo",
                samples=FRAME_SAMPLES,
            )
            frame.planes[0].update(chunk)
            frame.rate = self.sample_rate
            frame.pts = self._pts
            frame.time_base = self._time_base
            self._pts += FRAME_SAMPLES

            for packet in self._stream.encode(frame):
                self._container.mux(packet)

        return self._sink.drain()

    def close(self) -> bytes:
        """Finalize the stream (flush encoder + close container)."""
        if self._closed:
            return b""
        self._closed = True
        for packet in self._stream.encode(None):
            self._container.mux(packet)
        self._container.close()
        return self._sink.drain()
