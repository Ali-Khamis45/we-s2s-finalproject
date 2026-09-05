# M2 Moshi Bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `ml/moshi/relay.py`'s hand-rolled ctypes Ogg/Opus bridge
with a PyAV-backed bridge (`ml/moshi/bridge.py`) that fixes all three known
relay bugs (teardown segfault, crash on real speech, no ERROR translation),
add a codec/protocol test suite, and re-measure Moshi latency with real
speech content instead of the silence workaround.

**Architecture:** Same shape as the relay — one `websockets` server process
translating Kyutai's real Candle protocol (`wss://`, Opus-in-Ogg, tags 0-6)
to the plain `ws://`+PCM protocol `backend/app/services/moshi.py` already
speaks. The only structural change is a proven-in-spike PyAV codec layer
(replacing ctypes/PyOgg) plus explicit ERROR-tag translation. Encoder uses
`av.open(..., options={"page_duration": "20000"})` to flush a page per frame
fed; decoder runs PyAV's blocking demuxer in a worker thread bridged to
asyncio via a queue, with output resampled to 24kHz (Opus always decodes at
48kHz internally).

**Tech Stack:** Python 3.12, `websockets`, `av` (PyAV) 18.1.0, `pytest`,
`asyncio`.

## Global Constraints

- `backend/app/services/moshi.py` is NOT modified — it is the frozen client
  contract (Tag enum: `HANDSHAKE=0x00, AUDIO=0x01, TEXT=0x02, CONTROL=0x03, ERROR=0x04`).
- Bridge listens on `ws://127.0.0.1:8998/api/chat` (unchanged from the
  relay) and dials `wss://127.0.0.1:8999/api/chat` upstream (unchanged).
- PCM is 16-bit mono at 24kHz (`SAMPLE_RATE = 24_000`, `CHANNELS = 1`),
  matching `settings.moshi_sample_rate`.
- Encoder must feed/flush in **40ms (960-sample) frames** to match Candle's
  `stream_both.rs` PCM flush quantum (`24_000/25`) — 20ms silently stalls
  the upstream decoder after ~3 frames (found in M1, see
  `ml/moshi/bench_moshi_latency.py` docstring finding 1).
- `av.open(..., format="ogg", options={"page_duration": "20000"})` is
  required on the encoder side — without it, PyAV's muxer buffers ~1s of
  audio before emitting any bytes (verified by spike; see design spec).
- Decoded PCM must be resampled from Opus's fixed 48kHz output down to
  24kHz via `AudioResampler(format="s16", layout="mono", rate=24000)` —
  without this every duration is silently 2x wrong (verified by spike).
- `ml/moshi/requirements.txt`: `PyOgg>=0.6.14a1` removed, `av==18.1.0` added.
- Kyutai tag constants: `KT_HANDSHAKE, KT_AUDIO, KT_TEXT, KT_CONTROL, KT_METADATA, KT_ERROR, KT_PING = range(7)`.
  Our tag constants: `OUR_HANDSHAKE, OUR_AUDIO, OUR_TEXT, OUR_CONTROL, OUR_ERROR = range(5)`.

---

## File Structure

- `ml/moshi/bridge.py` (new) — the production bridge. Replaces `relay.py`.
  Contains: `OpusOggEncoder`, `OpusOggDecoder`, `bridge_upstream_to_client`,
  `bridge_client_to_upstream`, `handler`, `main`.
- `ml/moshi/relay.py` — deleted at the end, once `bridge.py` is proven.
- `ml/moshi/requirements.txt` — dependency swap.
- `ml/moshi/tests/__init__.py` (new, empty) — makes the tests dir a package
  so `pytest` discovers it without path hacks.
- `ml/moshi/tests/test_bridge_codec.py` (new) — encoder/decoder round-trip,
  chunked-feed, and page-latency tests. No GPU/Candle server required.
- `ml/moshi/tests/test_bridge_protocol.py` (new) — tag translation and
  ERROR-path tests against fake websocket doubles. No GPU/Candle server
  required.
- `ml/moshi/bench_moshi_latency.py` (modified) — point `RELAY_SCRIPT` at
  `bridge.py`, drop the per-iteration `RelayHandle` restart/retry
  machinery now that teardown doesn't segfault, and stream the WAV
  fixture's real decoded PCM instead of synthesized silence.
- `Makefile`, `scripts/make.ps1` (modified) — `moshi-serve` /
  manual-testing comments referencing `relay.py` updated to `bridge.py`.
- `docs/M1_BRINGUP_LOG.md` (modified) — append an M2 addendum noting the
  relay was replaced and where to look now (do not rewrite M1's history).
- `docs/PROJECT_PLAN.md` (modified) — mark M2 done, matching the M1 style.

---

## Task 1: Encoder — PCM to streaming Ogg/Opus via PyAV

**Files:**
- Create: `ml/moshi/bridge.py` (encoder portion only this task)
- Test: `ml/moshi/tests/test_bridge_codec.py`

**Interfaces:**
- Produces: `class OpusOggEncoder` with `__init__(self, sample_rate: int = 24_000, channels: int = 1) -> None`, `def feed(self, pcm: bytes) -> bytes` (accepts any amount of 16-bit PCM, buffers to 40ms frames internally, returns any complete Ogg page bytes produced by this call), `def close(self) -> bytes` (flushes and finalizes, returns final Ogg bytes).

- [ ] **Step 1: Write the failing test — encoder emits bytes without waiting for close()**

```python
# ml/moshi/tests/test_bridge_codec.py
"""Codec-layer tests for ml/moshi/bridge.py. No GPU or Candle server needed."""
from __future__ import annotations

import math
import struct

from ml.moshi.bridge import OpusOggEncoder, OpusOggDecoder

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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd "c:/Courses/WE Advanced AI/Final Project/we-s2s-finalproject" && backend/.venv/Scripts/python.exe -m pytest ml/moshi/tests/test_bridge_codec.py::test_encoder_emits_bytes_within_one_frame -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ml.moshi.bridge'` (or `ImportError`)

- [ ] **Step 3: Add `av` to `ml/moshi/requirements.txt`**

```
websockets>=12.0
av==18.1.0
```

(Remove the old `PyOgg>=0.6.14a1` line entirely.)

- [ ] **Step 4: Install the dependency into the backend venv**

Run: `cd "c:/Courses/WE Advanced AI/Final Project/we-s2s-finalproject" && backend/.venv/Scripts/python.exe -m pip install av==18.1.0`
Expected: `Successfully installed av-18.1.0`

- [ ] **Step 5: Write the encoder implementation**

```python
# ml/moshi/bridge.py
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
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd "c:/Courses/WE Advanced AI/Final Project/we-s2s-finalproject" && backend/.venv/Scripts/python.exe -m pytest ml/moshi/tests/test_bridge_codec.py::test_encoder_emits_bytes_within_one_frame -v`
Expected: `PASSED`

- [ ] **Step 7: Commit**

```bash
cd "c:/Courses/WE Advanced AI/Final Project/we-s2s-finalproject"
git add ml/moshi/bridge.py ml/moshi/requirements.txt ml/moshi/tests/test_bridge_codec.py
git commit -m "$(cat <<'EOF'
feat(m2): add PyAV-backed Ogg/Opus encoder for the Moshi bridge

Replaces relay.py's hand-rolled ctypes muxer. page_duration=20000 is
required to flush a page per fed frame -- without it PyAV's muxer
buffers about a second of audio before emitting any bytes, which a
spike during design confirmed is fatal for real-time streaming.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Decoder — chunked Ogg/Opus to PCM via PyAV, with resampling

**Revision note (2026-09-05):** the first version of this task specified a
synchronous `feed(chunk: bytes) -> bytes` decoder that reopens no state and
resumes one `container.demux(stream)` generator across calls. Implementation
found and this session independently confirmed that PyAV 18.1.0's Ogg
demuxer **permanently latches EOF** the first time its reader's `readinto`
returns 0 (its way of saying "no bytes available *right now*") — it does
not distinguish that from real end-of-stream, so a `feed()` that appends
bytes after the demuxer has already seen one empty `readinto` call produces
zero further packets forever, even with valid unread bytes sitting in the
buffer. Verified directly: a container opened on a partial buffer, demuxed
to exhaustion, then fed the rest of a 9964-byte Ogg stream on the same
container/stream objects — the second `demux()` call yields nothing.

The fix (verified by spike, 999ms decoded from 1000ms of encoded audio
through irregular chunking with real inter-call delays) is what the design
spec's Architecture section already called for and this task's original
version skipped: run `av.open()` and the demux loop **once**, on a
**blocking** reader, in a dedicated thread — `readinto` blocks on a queue
until bytes arrive rather than ever returning 0 for "nothing yet," so the
demuxer never sees a false EOF. This changes the class's public interface
from synchronous `feed() -> bytes` to `feed() -> None` (push bytes into the
worker) plus a separate `poll() -> bytes` (drain whatever's been decoded so
far) — the shape below is what actually ships.

**Files:**
- Modify: `ml/moshi/bridge.py` (add decoder)
- Test: `ml/moshi/tests/test_bridge_codec.py`

**Interfaces:**
- Consumes: nothing from Task 1 directly (independent codec direction), but shares `SAMPLE_RATE`/`CHANNELS`/`FRAME_SAMPLES` constants already defined in `bridge.py`.
- Produces: `class OpusOggDecoder` with `__init__(self, sample_rate: int = 24_000, channels: int = 1) -> None` (starts a worker thread), `def feed(self, chunk: bytes) -> None` (pushes raw Ogg/Opus bytes to the worker; non-blocking), `def poll(self, timeout: float = 0.05) -> bytes` (drains whatever 16-bit PCM has been decoded so far, resampled to `sample_rate`; blocks up to `timeout` waiting for at least the first item, then returns immediately with whatever else is already queued), `def close(self) -> None` (signals real EOF and joins the worker thread).

- [ ] **Step 1: Write the failing test — round trip through encoder+decoder preserves duration**

Append to `ml/moshi/tests/test_bridge_codec.py`:

```python
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
```

Add `import time` to the top of `ml/moshi/tests/test_bridge_codec.py` alongside its existing imports (`math`, `struct`) if not already present.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd "c:/Courses/WE Advanced AI/Final Project/we-s2s-finalproject" && backend/.venv/Scripts/python.exe -m pytest ml/moshi/tests/test_bridge_codec.py -v`
Expected: the two new tests FAIL with `ImportError` or `AttributeError: module 'ml.moshi.bridge' has no attribute 'OpusOggDecoder'`

- [ ] **Step 3: Write the decoder implementation**

Append to `ml/moshi/bridge.py`. Add `import queue` and `import threading` to the file's imports alongside the existing `fractions`/`io`/`logging` if not already present, and `from av.audio.resampler import AudioResampler` alongside the existing `import av`:

```python
class OpusOggDecoder:
    """Decodes a continuous, arbitrarily-chunked Ogg/Opus byte stream into PCM.

    Runs its own `av.open()` + demux loop once, on a dedicated thread, over a
    BLOCKING reader whose `readinto` waits on a queue for the next chunk
    instead of ever returning 0 for "nothing available yet." This is
    required, not a style choice: PyAV 18.1.0's Ogg demuxer treats a
    `readinto` returning 0 as permanent end-of-stream, so a synchronous
    feed()-returns-bytes design that lets the reader signal "nothing right
    now" via a 0 return breaks the first time the worker catches up to the
    buffered bytes -- confirmed by spike during this task's implementation
    (see this task's Revision note above). A blocking reader in its own
    thread sidesteps the issue entirely: the demuxer's `readinto` call
    simply blocks until real bytes (or close()'s EOF sentinel) arrive.

    Call `feed(chunk)` with whatever bytes arrived in one websocket message
    to push them to the worker (non-blocking). Call `poll()` to drain
    whatever 16-bit PCM has been decoded so far, resampled to `sample_rate`
    (Opus decodes at a fixed 48kHz internally regardless of the stream's
    negotiated rate -- see design spec finding 2). Call `close()` once no
    more input is coming, to signal real EOF and join the worker thread.
    """

    def __init__(self, sample_rate: int = SAMPLE_RATE, channels: int = CHANNELS) -> None:
        self.sample_rate = sample_rate
        self.channels = channels
        self._in_q: queue.Queue[bytes | None] = queue.Queue()
        self._out_q: queue.Queue[tuple[str, object]] = queue.Queue()
        self._resampler = AudioResampler(
            format="s16", layout="mono" if channels == 1 else "stereo", rate=sample_rate
        )
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    class _BlockingReader(io.RawIOBase):
        """readinto() blocks on the queue; only returns 0 for real EOF."""

        def __init__(self, in_q: "queue.Queue[bytes | None]") -> None:
            self._q = in_q
            self._buf = b""
            self._eof = False

        def readable(self) -> bool:
            return True

        def readinto(self, dst: bytearray) -> int:
            while not self._buf:
                if self._eof:
                    return 0
                item = self._q.get()  # blocks until feed() or close() posts
                if item is None:
                    self._eof = True
                    return 0
                self._buf = item
            n = min(len(dst), len(self._buf))
            dst[:n] = self._buf[:n]
            self._buf = self._buf[n:]
            return n

    def _run(self) -> None:
        reader = self._BlockingReader(self._in_q)
        try:
            container = av.open(reader, mode="r", format="ogg")
        except Exception as exc:
            self._out_q.put(("error", exc))
            return
        stream = container.streams.audio[0]
        try:
            for packet in container.demux(stream):
                for frame in packet.decode():
                    for out_frame in self._resampler.resample(frame):
                        pcm = bytes(out_frame.planes[0])[
                            : out_frame.samples * 2 * self.channels
                        ]
                        self._out_q.put(("pcm", pcm))
        except av.error.EOFError:
            pass
        except Exception as exc:
            self._out_q.put(("error", exc))
        container.close()
        self._out_q.put(("done", None))

    def feed(self, chunk: bytes) -> None:
        if chunk:
            self._in_q.put(chunk)

    def poll(self, timeout: float = 0.05) -> bytes:
        """Drain whatever PCM has been decoded so far.

        Blocks up to `timeout` waiting for at least one item, then drains
        anything else already queued without waiting further.
        """
        out = bytearray()
        try:
            kind, payload = self._out_q.get(timeout=timeout)
            if kind == "pcm":
                out += payload  # type: ignore[arg-type]
            elif kind == "error":
                raise payload  # type: ignore[misc]
            # "done": nothing to add, just stop waiting for more.
        except queue.Empty:
            return b""
        while True:
            try:
                kind, payload = self._out_q.get_nowait()
            except queue.Empty:
                break
            if kind == "pcm":
                out += payload  # type: ignore[arg-type]
            elif kind == "error":
                raise payload  # type: ignore[misc]
        return bytes(out)

    def close(self) -> None:
        self._in_q.put(None)
        self._thread.join(timeout=5)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd "c:/Courses/WE Advanced AI/Final Project/we-s2s-finalproject" && backend/.venv/Scripts/python.exe -m pytest ml/moshi/tests/test_bridge_codec.py -v`
Expected: all tests `PASSED`.

- [ ] **Step 5: Commit**

```bash
cd "c:/Courses/WE Advanced AI/Final Project/we-s2s-finalproject"
git add ml/moshi/bridge.py ml/moshi/tests/test_bridge_codec.py
git commit -m "$(cat <<'EOF'
feat(m2): add PyAV-backed Ogg/Opus decoder with 24kHz resampling

Runs the demux loop on a dedicated thread over a blocking reader,
not a synchronous feed()-returns-bytes design: PyAV 18.1.0's Ogg
demuxer permanently latches EOF the first time its reader returns 0,
so a reader that ever signals "nothing right now" that way breaks
after the first catch-up. A blocking reader avoids the false EOF
entirely. Also verified against single-byte-chunked input, the
adversarial case that triggered relay.py's ctypes segfault/crash
bugs, and confirms Opus's fixed 48kHz internal decode rate is
correctly resampled back to 24kHz.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Protocol bridging — asyncio server, tag translation, ERROR path

**Files:**
- Modify: `ml/moshi/bridge.py` (add server/handler logic)
- Test: `ml/moshi/tests/test_bridge_protocol.py` (new)

**Interfaces:**
- Consumes: `OpusOggEncoder` (Task 1) and `OpusOggDecoder` (Task 2) exactly as defined above — note Task 2's `OpusOggDecoder` interface is `feed(chunk: bytes) -> None` (push only) plus a separate `poll(timeout: float = 0.05) -> bytes` (drain decoded PCM), not a single `feed() -> bytes` call; `bridge_upstream_to_client` below calls both.
- Produces: `async def bridge_upstream_to_client(upstream, client) -> None`, `async def bridge_client_to_upstream(client, upstream) -> None`, `async def handler(client) -> None`, `async def main() -> None`.

- [ ] **Step 1: Write the failing tests — tag translation and ERROR path against fake websockets**

```python
# ml/moshi/tests/test_bridge_protocol.py
"""Protocol-translation tests for ml/moshi/bridge.py, using fake websocket
doubles. No GPU or Candle server needed."""
from __future__ import annotations

import asyncio

import pytest

from ml.moshi.bridge import (
    KT_AUDIO,
    KT_ERROR,
    KT_TEXT,
    OUR_AUDIO,
    OUR_ERROR,
    OUR_TEXT,
    bridge_upstream_to_client,
)


class FakeSocket:
    """A minimal async-iterable + .send() double standing in for a
    websockets connection object."""

    def __init__(self, incoming: list[bytes]) -> None:
        self._incoming = incoming
        self.sent: list[bytes] = []
        self.closed = False

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for frame in self._incoming:
            yield frame

    async def send(self, data: bytes) -> None:
        self.sent.append(data)


@pytest.mark.asyncio
async def test_text_tag_translated_and_forwarded() -> None:
    upstream = FakeSocket([bytes([KT_TEXT]) + b"hello"])
    client = FakeSocket([])
    await bridge_upstream_to_client(upstream, client)
    assert client.sent == [bytes([OUR_TEXT]) + b"hello"]


@pytest.mark.asyncio
async def test_upstream_error_tag_translated() -> None:
    upstream = FakeSocket([bytes([KT_ERROR]) + b"model crashed"])
    client = FakeSocket([])
    await bridge_upstream_to_client(upstream, client)
    assert client.sent == [bytes([OUR_ERROR]) + b"model crashed"]


@pytest.mark.asyncio
async def test_upstream_disconnect_sends_error_frame() -> None:
    """The relay's third gap: no ERROR translation when the upstream
    connection itself dies uncleanly. bridge_upstream_to_client must catch
    that and tell the client, rather than the client just seeing a close."""

    class DyingSocket:
        def __aiter__(self):
            return self._gen()

        async def _gen(self):
            yield bytes([KT_AUDIO]) + b"\x00\x00"
            raise ConnectionResetError("upstream closed uncleanly")

    upstream = DyingSocket()
    client = FakeSocket([])
    await bridge_upstream_to_client(upstream, client)
    assert client.sent, "expected at least one frame sent to client"
    last = client.sent[-1]
    assert last[0] == OUR_ERROR
    assert b"upstream closed uncleanly" in last[1:] or b"ConnectionResetError" in last[1:]
```

- [ ] **Step 2: Add `pytest-asyncio` to test dependencies and configure it**

Run: `cd "c:/Courses/WE Advanced AI/Final Project/we-s2s-finalproject" && backend/.venv/Scripts/python.exe -m pip install pytest-asyncio`
Expected: `Successfully installed pytest-asyncio-<version>`

Create `ml/moshi/tests/conftest.py`:

```python
# ml/moshi/tests/conftest.py
import pytest

pytest_plugins = ["pytest_asyncio"]


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "asyncio: mark test as async (pytest-asyncio)")
```

Create `ml/moshi/tests/pytest.ini`:

```ini
[pytest]
asyncio_mode = auto
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd "c:/Courses/WE Advanced AI/Final Project/we-s2s-finalproject" && backend/.venv/Scripts/python.exe -m pytest ml/moshi/tests/test_bridge_protocol.py -v`
Expected: FAIL with `ImportError: cannot import name 'bridge_upstream_to_client'`

- [ ] **Step 4: Write the server/handler implementation**

Append to `ml/moshi/bridge.py`:

```python
import asyncio
import contextlib
import ssl

import websockets


async def bridge_upstream_to_client(upstream, client) -> None:
    """Translate Candle's real protocol into ours, one direction.

    OpusOggDecoder decodes on its own worker thread (see Task 2's Revision
    note -- required by a real PyAV EOF-latching limitation, not a style
    choice), so this coroutine calls `decoder.poll()` via `asyncio.to_thread`
    to drain decoded PCM without blocking the event loop while `poll()`
    waits on its internal queue.

    Any failure reading from upstream -- a decode error, or the connection
    itself dying uncleanly -- is reported to the client as an OUR_ERROR
    frame before this coroutine returns, rather than the client just seeing
    an abrupt close (the relay's third known gap).
    """
    decoder = OpusOggDecoder()

    async def drain_decoded() -> None:
        """Forward decoded PCM to the client until cancelled.

        Runs as a concurrent task alongside receive_upstream() below, since
        PCM can finish decoding well after the last KT_AUDIO frame was fed
        to the (already-buffering) worker thread -- see the closing
        sequence below for why decoder.close() must complete before this
        task is cancelled.
        """
        while True:
            pcm = await asyncio.to_thread(decoder.poll, 0.05)
            if pcm:
                await client.send(bytes([OUR_AUDIO]) + pcm)

    async def receive_upstream() -> None:
        async for frame in upstream:
            if not frame:
                continue
            tag, payload = frame[0], frame[1:]
            log.debug("upstream->client: tag=%s payload_len=%d", tag, len(payload))
            if tag == KT_AUDIO:
                decoder.feed(payload)
            elif tag == KT_TEXT:
                await client.send(bytes([OUR_TEXT]) + payload)
            elif tag == KT_ERROR:
                await client.send(bytes([OUR_ERROR]) + payload)
            # KT_HANDSHAKE, KT_CONTROL, KT_METADATA, KT_PING: no equivalent
            # our client models: drop rather than guess at a mapping.

    try:
        drain_task = asyncio.create_task(drain_decoded())
        try:
            await receive_upstream()
        finally:
            # Signal real EOF and let the worker thread finish emitting any
            # PCM still in flight BEFORE cancelling the drain task -- verified
            # by spike that cancelling immediately drops 100% of a short
            # real-audio test case's output, since decode can finish shortly
            # after the last KT_AUDIO frame is fed.
            await asyncio.to_thread(decoder.close)
            drain_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await drain_task
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}".encode("utf-8", errors="replace")
        try:
            await client.send(bytes([OUR_ERROR]) + detail)
        except Exception:
            pass  # client already gone; nothing more to do


async def bridge_client_to_upstream(client, upstream) -> None:
    encoder = OpusOggEncoder()
    try:
        async for frame in client:
            if not frame:
                continue
            tag, payload = frame[0], frame[1:]
            log.debug("client->upstream: tag=%s payload_len=%d", tag, len(payload))
            if tag == OUR_AUDIO:
                ogg_bytes = encoder.feed(payload)
                if ogg_bytes:
                    await upstream.send(bytes([KT_AUDIO]) + ogg_bytes)
            # OUR_HANDSHAKE/OUR_CONTROL carry no payload our client sends today;
            # extend here if a later task starts sending them.
    finally:
        tail = encoder.close()
        if tail:
            try:
                await upstream.send(bytes([KT_AUDIO]) + tail)
            except Exception as exc:
                log.debug("  tail send failed: %r", exc)


async def handler(client) -> None:
    ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE  # local self-signed dev cert only
    log.info("client connected, dialing upstream %s", UPSTREAM_URL)
    try:
        async with websockets.connect(UPSTREAM_URL, ssl=ssl_ctx, max_size=None) as upstream:
            done, pending = await asyncio.wait(
                [
                    asyncio.create_task(bridge_upstream_to_client(upstream, client)),
                    asyncio.create_task(bridge_client_to_upstream(client, upstream)),
                ],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            for task in done:
                exc = task.exception()
                if exc:
                    log.info("bridge task ended: %r", exc)
    except Exception as exc:
        detail = f"could not reach Moshi: {type(exc).__name__}: {exc}"
        log.info(detail)
        try:
            await client.send(bytes([OUR_ERROR]) + detail.encode("utf-8", errors="replace"))
        except Exception:
            pass
    log.info("client disconnected")


async def main() -> None:
    async with websockets.serve(handler, LISTEN_HOST, LISTEN_PORT, max_size=None):
        print(f"bridge listening on ws://{LISTEN_HOST}:{LISTEN_PORT}/api/chat")
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd "c:/Courses/WE Advanced AI/Final Project/we-s2s-finalproject" && backend/.venv/Scripts/python.exe -m pytest ml/moshi/tests/test_bridge_protocol.py -v`
Expected: all `PASSED`

- [ ] **Step 6: Run the full bridge test suite together**

Run: `cd "c:/Courses/WE Advanced AI/Final Project/we-s2s-finalproject" && backend/.venv/Scripts/python.exe -m pytest ml/moshi/tests/ -v`
Expected: all tests `PASSED` (codec + protocol)

- [ ] **Step 7: Commit**

```bash
cd "c:/Courses/WE Advanced AI/Final Project/we-s2s-finalproject"
git add ml/moshi/bridge.py ml/moshi/tests/test_bridge_protocol.py ml/moshi/tests/conftest.py ml/moshi/tests/pytest.ini
git commit -m "$(cat <<'EOF'
feat(m2): wire bridge.py's asyncio server with ERROR-tag translation

Adds the websockets server/handler around the Task 1/2 codec classes,
plus explicit try/except in both bridge directions so an upstream
Candle failure or unclean disconnect reaches the client as an
OUR_ERROR frame instead of a bare close -- relay.py's third known gap.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Live validation against the running Candle server

**Files:**
- No new files. Manual validation task using `ml/moshi/bridge.py` from
  Tasks 1-3 and the already-running `moshi-backend.exe` (confirmed
  reachable at `https://localhost:8999/` during design).

**Interfaces:**
- Consumes: `ml/moshi/bridge.py`'s `main()` (starts the server) exactly as produced by Task 3.

- [ ] **Step 1: Confirm the Candle server is up**

Run: `curl -sk https://localhost:8999/ -o /dev/null -w "http_code=%{http_code}\n" --max-time 3`
Expected: `http_code=200`. If not 200, start it first: `cd "c:/Courses/WE Advanced AI/Final Project/we-s2s-finalproject" && CUDA_COMPUTE_CAP=120 (cd ml/moshi/candle-moshi/rust && ./target/release/moshi-backend.exe --config moshi-backend/config-q8.json standalone)` and wait for `"standalone worker listening on https://0.0.0.0:8999"` in its log before continuing.

- [ ] **Step 2: Start the bridge in the foreground**

Run (separate terminal / background process): `cd "c:/Courses/WE Advanced AI/Final Project/we-s2s-finalproject" && backend/.venv/Scripts/python.exe ml/moshi/bridge.py`
Expected: `bridge listening on ws://127.0.0.1:8998/api/chat` with no crash, and no `Segmentation fault` (the exact failure mode `relay.py` had on teardown — checked in Step 4 below, not this step).

- [ ] **Step 3: Stream real speech through it end to end**

Create a throwaway validation script `ml/moshi/_manual_speech_check.py`:

```python
"""Manual validation for M2 Task 4: stream real (non-silent) speech through
the bridge end to end and confirm audio + Inner Monologue text come back.
Deleted after Task 4 -- not part of the automated suite."""
from __future__ import annotations

import asyncio
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from app.services.moshi import MoshiAudio, MoshiText, moshi_client  # noqa: E402

WAV_PATH = "backend/scripts/words/dysfluent_utterance.wav"
CHUNK_MS = 40
CHUNK_BYTES = 24_000 * 2 * CHUNK_MS // 1000  # 40ms @ 24kHz, 16-bit mono


async def main() -> None:
    with wave.open(WAV_PATH, "rb") as wf:
        pcm = wf.readframes(wf.getnframes())

    async with moshi_client.session() as stream:
        got_audio = asyncio.Event()

        async def send() -> None:
            for i in range(0, len(pcm), CHUNK_BYTES):
                if got_audio.is_set():
                    return
                await stream.send_audio(pcm[i : i + CHUNK_BYTES])
                await asyncio.sleep(CHUNK_MS / 1000)

        async def recv() -> None:
            async for event in stream.events():
                if isinstance(event, MoshiAudio):
                    print("got audio:", len(event.pcm), "bytes")
                    got_audio.set()
                    return
                if isinstance(event, MoshiText):
                    print("inner monologue:", event.text)

        await asyncio.wait_for(asyncio.gather(send(), recv()), timeout=15)


if __name__ == "__main__":
    asyncio.run(main())
```

Run: `cd "c:/Courses/WE Advanced AI/Final Project/we-s2s-finalproject" && backend/.venv/Scripts/python.exe ml/moshi/_manual_speech_check.py`
Expected: `got audio: <N> bytes` printed (possibly preceded by `inner monologue:` lines), with no exception and no `OUR_ERROR` frame surfaced as an exception — this is the case (real, non-silent speech content) that reliably crashed the client's connection to `relay.py` within milliseconds. Note the bridge's own terminal (Step 2) for any `Segmentation fault` line — there should be none.

- [ ] **Step 4: Repeat connect/disconnect against the same bridge process to check teardown**

Run Step 3's script three more times in a row, back to back, **without restarting the bridge process from Step 2**:

Run: `cd "c:/Courses/WE Advanced AI/Final Project/we-s2s-finalproject" && for i in 1 2 3; do backend/.venv/Scripts/python.exe ml/moshi/_manual_speech_check.py; done`
Expected: all three runs print `got audio: ...` and complete cleanly. The bridge process from Step 2 must still be running and printing `client connected` / `client disconnected` log lines after each — no `Segmentation fault`, no silent exit. This is the direct regression check for the relay's first known bug (segfault on connection teardown), which only manifested on the *second or later* connection to one relay process.

- [ ] **Step 5: Stop the bridge and remove the throwaway script**

Stop the Step 2 process (Ctrl+C in its terminal, or `TaskStop` if it was launched as a background task).

Run: `cd "c:/Courses/WE Advanced AI/Final Project/we-s2s-finalproject" && rm ml/moshi/_manual_speech_check.py`

- [ ] **Step 6: Commit (log entry only — no code changed this task)**

Append a short section to `docs/M1_BRINGUP_LOG.md` (do not edit its existing content):

```markdown

## M2 addendum (2026-09-05)

`ml/moshi/relay.py` has been replaced by `ml/moshi/bridge.py` (PyAV-backed
codec layer). All three bugs documented above under "Known limitations" are
fixed and verified against the live Candle server with real speech content:
no segfault across repeated connect/disconnect cycles on one bridge
process, no crash on real (non-silent) speech PCM, and upstream failures
now reach the client as an `OUR_ERROR` frame. See
`docs/superpowers/specs/2026-09-05-m2-moshi-bridge-design.md` for the full
design record and the pre-implementation spike findings that drove it.
```

```bash
cd "c:/Courses/WE Advanced AI/Final Project/we-s2s-finalproject"
git add docs/M1_BRINGUP_LOG.md
git commit -m "$(cat <<'EOF'
docs(m2): record live validation of the PyAV bridge against Candle

Confirmed against the running moshi-backend.exe with real speech
content and repeated connect/disconnect cycles: no segfault on
teardown, no crash on real speech, and upstream failures now reach
the client as an OUR_ERROR frame -- all three relay.py bugs closed.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Update the latency benchmark and task-runner wiring

**Files:**
- Modify: `ml/moshi/bench_moshi_latency.py`
- Modify: `Makefile`
- Modify: `scripts/make.ps1`

**Interfaces:**
- Consumes: `ml/moshi/bridge.py` (Tasks 1-4) as a drop-in replacement for `ml/moshi/relay.py` at the same `RELAY_PYTHON`/port/protocol contract.

- [ ] **Step 1: Update `bench_moshi_latency.py` to target `bridge.py` and drop the disposable-relay workaround**

In `ml/moshi/bench_moshi_latency.py`, replace the module docstring's findings 2-3 and the `RELAY_SCRIPT`/`RELAY_MANAGED` setup, since both existed specifically to route around bugs Task 1-4 fixed:

```python
"""Moshi Candle server latency: time-to-first-audio, p50/p95.

Mirrors backend/scripts/bench_latency.py's reporting format so the numbers
are directly comparable for M12 (Moshi vs cascade).

Adapted from the original Task 6 draft: Moshi is a full-duplex streaming
model, so a single short send-and-wait round trip (the original draft's
pattern) never produces an audio event. This script instead streams audio
continuously as paced PCM chunks, sending and receiving concurrently, and
measures wall-clock time from "start of streaming" to the first `MoshiAudio`
event received back -- matching how a live conversation would actually use
this client.

M2 update (2026-09-05): this script now targets ml/moshi/bridge.py, the
PyAV-backed replacement for M1's ml/moshi/relay.py. bridge.py fixes the
three relay bugs this script previously had to route around (see
docs/superpowers/specs/2026-09-05-m2-moshi-bridge-design.md and
docs/M1_BRINGUP_LOG.md's M2 addendum for the full account):

1. Chunk size remains 40ms (960 samples @ 24kHz) -- this was never a relay
   bug, it is Candle's own stream_both.rs PCM flush quantum
   (`size_in_buf >= 24_000/25`), and bridge.py's encoder is built around it
   directly (see FRAME_MS in bridge.py).
2. bridge.py does not segfault on connection teardown, so this script no
   longer restarts it per iteration -- one bridge process is started once
   and reused across all N iterations.
3. bridge.py's PyAV-based Opus encoder does not crash on real speech
   content, so this script now streams the WAV fixture's actual decoded
   PCM instead of synthesized silence.

Usage: python ml/moshi/bench_moshi_latency.py <wav_path> <N>
"""
from __future__ import annotations

import asyncio
import statistics
import subprocess
import sys
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from app.services.moshi import moshi_client, MoshiAudio  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
BRIDGE_SCRIPT = REPO_ROOT / "ml" / "moshi" / "bridge.py"
BRIDGE_PYTHON = REPO_ROOT / "backend" / ".venv" / "Scripts" / "python.exe"
BRIDGE_LOG = REPO_ROOT / "ml" / "moshi" / "_bench_bridge.log"

#: Chunk size in ms. MUST match the Candle server's internal PCM flush
#: quantum (40ms == 24_000/25 samples in stream_both.rs's spawn_recv_loops).
CHUNK_MS = 40

#: How long to stream before giving up on one iteration.
RUN_TIMEOUT_S = 8.0

#: How many times to retry one iteration before giving up and excluding it.
#: Kept at a small value (not 0) because a single dropped iteration due to
#: e.g. a transient GPU scheduling hiccup shouldn't fail the whole run --
#: but bridge.py's stability means this is no longer routing around a known
#: crash, just ordinary flakiness tolerance.
MAX_RETRIES = 2


def report(name: str, samples: list[float], unit: str = "ms") -> None:
    p50 = statistics.median(samples)
    p95 = sorted(samples)[int(len(samples) * 0.95)] if len(samples) > 1 else samples[0]
    print(f"{name}: p50={p50:.1f}{unit} p95={p95:.1f}{unit} n={len(samples)}")


def load_fixture_pcm(wav_path: str, chunk_ms: int) -> list[bytes]:
    """Return the WAV fixture's own decoded PCM, chunked to chunk_ms frames.

    M2 update: bridge.py's Opus encoder does not crash on real speech (see
    module docstring), so this streams the fixture's actual content instead
    of synthesized silence -- the first honest content-bearing
    time-to-first-audio measurement for M12.
    """
    with wave.open(wav_path, "rb") as wf:
        assert wf.getframerate() == 24_000, "expects 24kHz PCM to match moshi_sample_rate"
        assert wf.getnchannels() == 1, "expects mono PCM"
        assert wf.getsampwidth() == 2, "expects 16-bit PCM"
        pcm = wf.readframes(wf.getnframes())

    bytes_per_ms = 24_000 * 2 // 1000
    chunk_bytes = bytes_per_ms * chunk_ms
    chunks = [pcm[i : i + chunk_bytes] for i in range(0, len(pcm), chunk_bytes)]
    if chunks and len(chunks[-1]) < chunk_bytes:
        chunks[-1] = chunks[-1] + b"\x00" * (chunk_bytes - len(chunks[-1]))
    return chunks


class BridgeHandle:
    """Manages ml/moshi/bridge.py as one long-lived subprocess reused across
    all iterations (bridge.py does not segfault on teardown, so the
    per-iteration restart M1's relay needed is no longer necessary)."""

    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self._log = None

    def start(self) -> None:
        self._log = open(BRIDGE_LOG, "ab")
        self._proc = subprocess.Popen(
            [str(BRIDGE_PYTHON), str(BRIDGE_SCRIPT)],
            stdout=self._log,
            stderr=self._log,
            cwd=str(REPO_ROOT),
        )

    def stop(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._proc = None
        if self._log is not None:
            self._log.close()
            self._log = None

    async def wait_ready(self, timeout_s: float = 10.0) -> None:
        """Poll via the real client's own readiness probe until the bridge
        (and its upstream connection to Candle) is actually ready."""
        moshi_client.invalidate()
        deadline = time.perf_counter() + timeout_s
        last_exc: Exception | None = None
        while time.perf_counter() < deadline:
            try:
                if await moshi_client.available(force=True):
                    return
            except Exception as exc:  # pragma: no cover - defensive
                last_exc = exc
            await asyncio.sleep(0.3)
        suffix = f": {last_exc}" if last_exc else ""
        raise RuntimeError(f"bridge did not become ready in time{suffix}")


async def one_run(chunks: list[bytes]) -> float | None:
    """Stream chunks continuously, paced at ~CHUNK_MS intervals, and measure
    wall-clock time from the start of streaming to the first MoshiAudio event.
    Returns None on timeout/failure (caller decides whether to retry)."""
    start = time.perf_counter()
    got = asyncio.Event()
    result: dict[str, float] = {}

    async with moshi_client.session() as stream:

        async def sender() -> None:
            while not got.is_set():
                for chunk in chunks:
                    if got.is_set():
                        return
                    await stream.send_audio(chunk)
                    await asyncio.sleep(CHUNK_MS / 1000)

        async def receiver() -> None:
            async for event in stream.events():
                if isinstance(event, MoshiAudio):
                    result["ms"] = (time.perf_counter() - start) * 1000
                    got.set()
                    return

        try:
            await asyncio.wait_for(asyncio.gather(sender(), receiver()), timeout=RUN_TIMEOUT_S)
        except asyncio.TimeoutError:
            got.set()
        except Exception:
            got.set()

    return result.get("ms")


async def run_iteration(chunks: list[bytes]) -> tuple[float, int]:
    """One measured iteration against the already-running, shared bridge.
    Retries a small number of times for ordinary flakiness, not to route
    around a known crash (see MAX_RETRIES)."""
    for attempt in range(1, MAX_RETRIES + 1):
        moshi_client.invalidate()
        if not await moshi_client.available(force=True):
            print(f"  attempt {attempt}: Moshi not reachable, retrying")
            await asyncio.sleep(1)
            continue

        elapsed = await one_run(chunks)
        if elapsed is not None:
            return elapsed, attempt
        print(f"  attempt {attempt}: no audio event within {RUN_TIMEOUT_S:.0f}s, retrying")

    raise RuntimeError(f"iteration failed after {MAX_RETRIES} attempts")


async def main(wav_path: str, n: int) -> None:
    chunks = load_fixture_pcm(wav_path, CHUNK_MS)
    print(f"fixture {wav_path}: {len(chunks)} x {CHUNK_MS}ms chunks of real decoded speech "
          f"(M2: bridge.py's Opus encoder handles real content, unlike M1's relay.py)")

    bridge = BridgeHandle()
    bridge.start()
    try:
        await bridge.wait_ready()
        samples: list[float] = []
        for i in range(n):
            elapsed, attempts = await run_iteration(chunks)
            note = f" (RETRIED, {attempts} attempts)" if attempts > 1 else ""
            print(f"  iteration {i + 1}/{n}: {elapsed:.1f}ms{note}")
            samples.append(elapsed)
        report("time-to-first-audio", samples)
    finally:
        bridge.stop()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], int(sys.argv[2])))
```

- [ ] **Step 2: Run the updated benchmark against the live Candle server**

Run: `cd "c:/Courses/WE Advanced AI/Final Project/we-s2s-finalproject" && backend/.venv/Scripts/python.exe ml/moshi/bench_moshi_latency.py backend/scripts/words/dysfluent_utterance.wav 10`
Expected: a `time-to-first-audio: p50=...ms p95=...ms n=10` line, with no iteration needing more than 1 attempt (bridge.py should not be flaky the way `relay.py` was). Record this p50/p95 — it is the number [[thesis-latency-narrative]] needs and will very likely differ from M1's silence-only 1.6s/1.76-1.81s figures, since this is now real speech content end to end.

- [ ] **Step 3: Update `Makefile`'s `moshi-serve`/`moshi-bench` comments**

In `Makefile`, change:

```makefile
moshi-serve: ## Start Kyutai's Candle server (needs GPU + weights; not the relay)
	@echo "NOTE: this bring-up was done and verified on Windows; scripts/make.ps1 moshi-serve"
	@echo "is the tested path. This target is provided for parity but is untested on Linux/macOS"
	@echo "(binary name and venv layout below assume a Windows build)."
	@echo "candle server https://localhost:8999 (internal, Kyutai's real protocol)"
	@echo "relay is NOT started here -- moshi-bench starts its own; for manual"
	@echo "testing run: $(BACKEND)/.venv/Scripts/python.exe ml/moshi/relay.py"
	CUDA_COMPUTE_CAP=120 sh -c 'cd ml/moshi/candle-moshi/rust && ./target/release/moshi-backend.exe --config moshi-backend/config-q8.json standalone'
```

to:

```makefile
moshi-serve: ## Start Kyutai's Candle server (needs GPU + weights; not the bridge)
	@echo "NOTE: this bring-up was done and verified on Windows; scripts/make.ps1 moshi-serve"
	@echo "is the tested path. This target is provided for parity but is untested on Linux/macOS"
	@echo "(binary name and venv layout below assume a Windows build)."
	@echo "candle server https://localhost:8999 (internal, Kyutai's real protocol)"
	@echo "bridge is NOT started here -- moshi-bench starts its own; for manual"
	@echo "testing run: $(BACKEND)/.venv/Scripts/python.exe ml/moshi/bridge.py"
	CUDA_COMPUTE_CAP=120 sh -c 'cd ml/moshi/candle-moshi/rust && ./target/release/moshi-backend.exe --config moshi-backend/config-q8.json standalone'
```

- [ ] **Step 4: Update `scripts/make.ps1`'s `moshi-serve` block**

In `scripts/make.ps1`, change the `"moshi-serve"` case's comment block and echo lines:

```powershell
    "moshi-serve" {
        # Just the Candle server -- moshi-bench manages its own bridge
        # subprocess, started once and reused across iterations (see
        # ml/moshi/bench_moshi_latency.py); for interactive/manual testing
        # the bridge is a separate foreground process
        # (`backend\.venv\Scripts\python.exe ml\moshi\bridge.py`).
        # So this target's only job is: bring up the Candle server on 8999
        # and confirm it's listening.
        Write-Host "candle server https://localhost:8999 (internal, Kyutai's real protocol)"
        Write-Host "bridge is NOT started here -- moshi-bench starts its own; for manual"
        Write-Host "testing run: backend\.venv\Scripts\python.exe ml\moshi\bridge.py"
        $rustDir = Join-Path $Repo "ml\moshi\candle-moshi\rust"
        $exe = Join-Path $rustDir "target\release\moshi-backend.exe"
        if (-not (Test-Path $exe)) { throw "moshi-backend.exe not found -- build it first (see docs/M1_BRINGUP_LOG.md)" }
        foreach ($pem in "key.pem", "cert.pem") {
            if (-not (Test-Path (Join-Path $rustDir $pem))) {
                throw "$pem missing in $rustDir -- generate the TLS cert first (see docs/M1_BRINGUP_LOG.md)"
            }
        }
        $env:CUDA_COMPUTE_CAP = "120"
        Push-Location $rustDir
        try {
            & $exe --config "moshi-backend/config-q8.json" standalone
        } finally {
            Pop-Location
        }
    }
```

- [ ] **Step 5: Delete the old relay and its stray artifacts**

Run: `cd "c:/Courses/WE Advanced AI/Final Project/we-s2s-finalproject" && rm -f ml/moshi/relay.py ml/moshi/_relay.log ml/moshi/_relay_err.log ml/moshi/_bench_relay.log && rm -rf ml/moshi/__pycache__`

- [ ] **Step 6: Run the full test suite one more time to confirm nothing referenced the deleted file**

Run: `cd "c:/Courses/WE Advanced AI/Final Project/we-s2s-finalproject" && backend/.venv/Scripts/python.exe -m pytest ml/moshi/tests/ -v`
Expected: all `PASSED`

- [ ] **Step 7: Commit**

```bash
cd "c:/Courses/WE Advanced AI/Final Project/we-s2s-finalproject"
git add ml/moshi/bench_moshi_latency.py Makefile scripts/make.ps1
git rm ml/moshi/relay.py
git add -A ml/moshi
git commit -m "$(cat <<'EOF'
feat(m2): point moshi-bench at bridge.py, stream real speech, remove relay.py

bridge.py's per-connection stability means the benchmark no longer
needs to restart a disposable relay process every iteration, and its
PyAV encoder handles real (non-silent) speech, so the benchmark now
measures genuine content-bearing time-to-first-audio for the first
time instead of the silence-only workaround M1 was forced into.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Update project docs to mark M2 done

**Files:**
- Modify: `docs/PROJECT_PLAN.md`

**Interfaces:**
- Consumes: the measured p50/p95 numbers from Task 5, Step 2.

- [ ] **Step 1: Mark M2 done in the Track M table, matching M1's style**

In `docs/PROJECT_PLAN.md`, find the M2 row (search for `| M2 |`) and prepend a `✅ **DONE (2026-09-05).**` marker plus the measured numbers, following exactly the pattern the M1 row already uses:

```markdown
| M2 | ✅ **DONE (2026-09-05).** `ml/moshi/bridge.py` (PyAV-backed) replaces M1's throwaway relay, fixing all three known bugs (teardown segfault, crash on real speech, missing ERROR translation). Measured time-to-first-audio with real speech content: p50=<FILL_FROM_TASK5_STEP2>ms/p95=<FILL_FROM_TASK5_STEP2>ms. Full record: `docs/superpowers/specs/2026-09-05-m2-moshi-bridge-design.md` and `docs/M1_BRINGUP_LOG.md`'s M2 addendum. | Flagship model service |
```

Replace `<FILL_FROM_TASK5_STEP2>` with the actual p50/p95 values recorded in Task 5, Step 2 — do not leave the placeholder text in the committed file.

- [ ] **Step 2: Commit**

```bash
cd "c:/Courses/WE Advanced AI/Final Project/we-s2s-finalproject"
git add docs/PROJECT_PLAN.md
git commit -m "$(cat <<'EOF'
docs: mark M2 complete in PROJECT_PLAN with measured real-speech latency

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```