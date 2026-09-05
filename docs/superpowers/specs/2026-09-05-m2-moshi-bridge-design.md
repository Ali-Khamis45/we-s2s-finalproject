# M2 — Production Moshi Bridge Service (design)

Date: 2026-09-05
Status: approved

## Context

M1 left `ml/moshi/relay.py`: a working but explicitly throwaway Python bridge
translating Kyutai's real Candle wire protocol (`wss://`, Opus-in-Ogg audio,
tags 0-6, see `rust/protocol.md`) to the plain `ws://` + raw-PCM protocol
`backend/app/services/moshi.py` already speaks. It has three known bugs, all
traced to one root cause: a hand-rolled ctypes Ogg/Opus muxer/demuxer built
against `PyOgg` 0.6.14a1, which lacks streaming-friendly high-level bindings
and has at least two internal wrapper bugs (see `docs/M1_BRINGUP_LOG.md`).

1. Segfaults on connection teardown (native crash, no Python traceback).
2. Its Opus encoder crashes the client connection on real speech content
   (works fine on silence).
3. No `ERROR`-tag translation when the upstream Candle connection itself
   fails to connect — client just sees an abrupt close.

M2's job (per `docs/PROJECT_PLAN.md`) is "Moshi streaming service: WebSocket
server wrapping Candle, exposing audio in/out plus the Inner Monologue text
stream." `backend/app/services/moshi.py` is the frozen client contract this
project builds toward — it must not need to change.

## Decisions made during design

**Bridge stays a separate Python process (not merged into Candle's Rust
server, not absorbed into the FastAPI backend).** A Rust-side native
plain-PCM endpoint inside `moshi-backend` would remove the Opus round-trip
entirely (likely the single biggest latency win available), but requires
patching a vendored upstream Rust codebase and rebuilding through the
documented CUDA/MSVC/CMake toolchain on every iteration — the exact loop M1
found painful. Teaching the FastAPI backend Kyutai's protocol directly
would just relocate the PCM transcode into Track A code and violate the
plan's "that file was never modified" contract. A bridge is the intended
architecture per the plan; the relay's bugs are all attributable to its one
replaceable component, not to the bridge pattern itself.

**Codec library: PyAV** (bindings over ffmpeg's libavformat/libavcodec),
replacing `PyOgg` + hand-rolled ctypes Ogg framing entirely. Verified via
spike (2026-09-05, this session) before committing to the design:

- Installs cleanly as a single wheel (`av==18.1.0`, 27.6MB, bundled ffmpeg)
  on this box's Python 3.12/Windows venv — no system ffmpeg, no build tools.
- **Finding 1 — the muxer buffers by default.** A naive `av.open(..., format="ogg")`
  emitted bytes for only 1 of 25 fed frames, dumping the rest at `close()` —
  a ~1s stall, fatal for real-time streaming. Fixed with
  `options={"page_duration": "20000"}` (µs), which forces a flushed Ogg page
  per frame fed — confirmed 25/25 frames producing output immediately.
  Re-verified at Candle's actual 40ms/960-sample PCM flush quantum (see
  Finding 3 below): still 15/15 frames flush immediately at that frame size.
  This is the maintained-library equivalent of the relay's manual
  `ogg_stream_flush`-per-packet workaround, now a declared option instead of
  an artisanal one.
- **Finding 2 — Opus always decodes at 48kHz internally**, regardless of the
  stream's negotiated/encoded rate. Demuxing spike-generated 24kHz-source
  Ogg/Opus without resampling silently produced PCM at 2x the expected
  sample count. Fixed by pushing decoded frames through
  `AudioResampler(format="s16", layout="mono", rate=24000)` before handing
  PCM to the client. The relay never hit this because its ctypes call to
  `opus_decode` was told 24000 directly.
- **Finding 3 — Candle's PCM flush quantum is 40ms (960 samples @ 24kHz),
  not 20ms.** `bench_moshi_latency.py`'s own docstring (Task 6) already
  documents that 20ms chunks silently stalled the Candle server's Ogg/Opus
  decoder after ~3 frames; the encoder side must feed/flush at 40ms to
  match `stream_both.rs`'s expectation. Verified during this spike that
  `page_duration=20000` still flushes correctly per 40ms frame (page
  duration is independent of the fed frame size).
- Demux was also verified against **irregularly-chunked input** (byte
  boundaries that don't align to Ogg page or websocket message boundaries)
  with no crash — the structural case the relay's segfault/speech-crash bugs
  lived in.

These three findings replace assumptions with proven configuration; without
the spike, Finding 1 (silent 1s buffering) and Finding 2 (silent 2x duration
bug) would very likely have surfaced only after the async bridge was fully
wired up, as confusing runtime behavior rather than a config line.

**Scope: functional parity + tests, not full productionization.** See
"Out of scope" below for what this explicitly excludes and why.

## Architecture

Same overall shape as `relay.py`: one process, `ml/moshi/bridge.py`
(replacing `relay.py`), a `websockets` server on `ws://127.0.0.1:8998/api/chat`
dialing out to Candle's `wss://127.0.0.1:8999/api/chat`. Per client
connection: one encoder instance (client PCM → Ogg/Opus upstream) and one
decoder instance (Ogg/Opus from upstream → client PCM), run as two
concurrent tasks per connection exactly as `relay.py` already structures it
(`bridge_client_to_upstream` / `bridge_upstream_to_client` via
`asyncio.wait(..., FIRST_COMPLETED)`).

**Encoder.** `av.open(sink, mode="w", format="ogg", options={"page_duration": "20000"})`
where `sink` is a small `io.RawIOBase` subclass that captures muxer writes
for immediate forwarding over the websocket. Fed 40ms (960-sample) PCM
frames to match Candle's flush quantum.

**Decoder.** PyAV's demuxer is synchronous/pull-based
(`inp.demux(stream)` blocks waiting for bytes), which does not fit directly
into an asyncio receive loop. Bridge this with:
- A blocking `io.RawIOBase` subclass whose `readinto` pulls from a
  thread-safe queue.
- The async receive loop (`async for frame in upstream`) pushes received
  bytes into that queue.
- The actual `av.open(...)` + demux-and-resample loop runs via
  `asyncio.to_thread`, publishing decoded/resampled PCM back to the async
  side through an `asyncio.Queue` (thread-safe handoff via
  `loop.call_soon_threadsafe`).
- Decoded frames pass through `AudioResampler(format="s16", layout="mono", rate=24000)`
  before being queued for the client.

**Error translation.** `bridge_upstream_to_client` wraps the upstream
connect and receive loop in `try/except`; a failed `websockets.connect` or
an uncleanly-closed upstream socket sends one `OUR_ERROR` frame (tag +
short diagnostic string) to the client before the connection closes. Pure
protocol logic, no codec dependency, addresses the relay's third gap
directly.

**Teardown.** Both PyAV contexts get explicit `close()` in `finally` blocks
matching the existing per-connection task-cancellation structure — no new
concurrency pattern, just codec objects that don't corrupt memory when
closed (the actual root cause of the relay's segfault).

## Dependency change

`ml/moshi/requirements.txt`: remove `PyOgg>=0.6.14a1`, add `av==18.1.0`
(pin the version verified during this spike).

## Testing

`ml/moshi/tests/test_bridge_codec.py` (no GPU/Candle server required):
- Encode→decode round trip on synthetic PCM (tone *and* silence — silence
  alone is what let the relay's bug hide in M1's own bench).
- Assert output PCM duration and sample rate match input (regression guard
  for Finding 2).
- Chunked-feed test: demux input split at irregular byte boundaries not
  aligned to Ogg pages or plausible websocket message sizes.
- Page-latency test: assert encoded bytes are emitted within one frame of
  being fed, at Candle's real 40ms frame size (regression guard for
  Finding 1 / Finding 3).

`ml/moshi/tests/test_bridge_protocol.py` (no GPU/Candle server required):
- Tag translation table (`KT_*` ↔ `OUR_*`), using fake upstream/client
  websocket doubles.
- New `ERROR`-path translation on simulated upstream connect failure and
  simulated uncleanly-closed upstream socket.

## Validation before M2 is done

1. Round-trip against the **live Candle server** with real recorded speech
   (not silence) through both directions — the case that crashed the relay
   — over a multi-minute session with repeated connect/disconnect to check
   the teardown fix under real conditions, not just synthetic spikes.
2. Re-run `ml/moshi/bench_moshi_latency.py` **unchanged**, but now able to
   stream the WAV fixture's actual decoded speech instead of the
   silence-only workaround the relay's bugs forced — the first honest
   content-bearing time-to-first-audio number, feeding the M12 thesis
   comparison.
3. Update `moshi-bench`'s `RelayHandle` restart-per-iteration logic
   (`Makefile` / `scripts/make.ps1`) to reuse one bridge process across
   iterations instead of restarting per iteration, since that restart
   existed solely to work around the segfault-on-teardown bug.

## Out of scope (deferred, not dropped)

Process supervision, auto-reconnect, and folding the bridge into
`moshi-serve`'s single command. M1's log shows the current manual-start
split (`moshi-serve` brings up only Candle; the bridge is started
separately) was a direct consequence of relay instability. Once this
design proves the bridge stable under real speech and repeated
connect/disconnect, merging it into `moshi-serve` is a natural fast
follow-up — but it is not required to satisfy M2's stated deliverable, and
folding it in now would scope-creep a codec rewrite into a service-
supervision project.

## Files touched

- `ml/moshi/bridge.py` (new, replaces `ml/moshi/relay.py`)
- `ml/moshi/relay.py` (removed)
- `ml/moshi/requirements.txt` (swap `PyOgg` → `av`)
- `ml/moshi/tests/test_bridge_codec.py` (new)
- `ml/moshi/tests/test_bridge_protocol.py` (new)
- `Makefile`, `scripts/make.ps1` (`moshi-bench` target: stop restarting the
  bridge per iteration)
- `docs/M1_BRINGUP_LOG.md` or a new `docs/M2_BRIDGE_LOG.md` (implementation
  log, written during execution — not part of this design)
- `docs/PROJECT_PLAN.md` (mark M2 done on completion)
