# M1 Bring-Up Log — Moshi on RTX 5050

## Environment
- Driver: 610.74 (CUDA UMD 13.3)
- CUDA Toolkit: 13.3 (V13.3.73), installed 2026-09-05, Custom install, display driver component unchecked to avoid downgrading the existing 610.74 driver
- CUDA Toolkit 12.8.61 ALSO installed side-by-side 2026-09-05 (`C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.8\`), same custom/no-driver options. Required because `cudarc` 0.16.2 (a Candle dependency) hardcodes a supported-version table that tops out at 12.8 and panics on any newer `nvcc --version` string, including 13.3. The build puts v12.8's `bin` ahead of PATH via `CUDA_PATH`/`PATH` env vars set only for the build invocation — the system-wide CUDA install remains 13.3.
- Rust: 1.98.1 (rustup, stable-x86_64-pc-windows-msvc), installed 2026-09-05
- Kyutai moshi repo commit: e6a55d2722a65870ef52a6c9f6ecfc0e90f38362

## Build blockers found and fixed (2026-09-05)
1. `cudarc` doesn't recognize CUDA 13.3 → installed CUDA 12.8 side-by-side, pointed PATH/CUDA_PATH at it for the build only.
2. `nvcc fatal: Cannot find compiler 'cl.exe' in PATH` → needed to run the build from a shell with Visual Studio 2022's `vcvars64.bat` sourced first (`C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat`).
3. `sentencepiece-sys`'s vendored CMakeLists.txt declares `cmake_minimum_required` below what CMake 4.3.3 (installed on this machine) accepts → set env var `CMAKE_POLICY_VERSION_MINIMUM=3.5` before the build.

With all three fixed: `cargo build --release --features cuda --bin moshi-backend` succeeds in ~3m28s, producing `ml/moshi/candle-moshi/rust/target/release/moshi-backend.exe` (44.9 MB).

## Weight variant used
q8 (kyutai/moshiko-candle-q8), auto-fetched by moshi-backend on first run to
`C:\Users\youss\.cache\huggingface\hub\`. Server logs `dtype=BF16` at warm-up
(the LM head/backbone load path reports its runtime dtype as BF16 even for the
q8-quantized weights — this is the Candle server's own log line, not a sign
q8 wasn't used; config-q8.json's `hf_repo` was the one requested).

## Working serve command (CONFIRMED WORKING 2026-09-05)
From `ml/moshi/candle-moshi/rust/`, with key.pem/cert.pem generated via
`MSYS_NO_PATHCONV=1 openssl req -x509 -newkey rsa:4096 -keyout key.pem -out cert.pem -days 365 -nodes -subj "/CN=localhost"`
(the `MSYS_NO_PATHCONV=1` prefix is required in Git Bash / MSYS shells — without
it, MSYS rewrites `/CN=localhost` into a bogus Windows path):

    cd ml/moshi/candle-moshi/rust
    ./target/release/moshi-backend.exe --config moshi-backend/config-q8.json standalone

Confirmed: `curl -sk https://localhost:8998/` returns HTTP 200. Server log:
"standalone worker listening on https://0.0.0.0:8998", model warmed up on
`Cuda(DeviceId(1))`. Time from process start to "model is ready to roll!" was
~65 minutes — dominated by the first-run weight download, not GPU warm-up.

## Measured latency (time to first audio)
TBD — pending Task 6 (needs the relay from Task 5 first, since backend/app/services/moshi.py
cannot speak Kyutai's real protocol directly)

## Task 5: Protocol relay (Opus/Ogg <-> PCM, tag remap, wss:// <-> ws://)

`ml/moshi/relay.py`, listening on `ws://127.0.0.1:8998/api/chat`, bridges Kyutai's
real Candle server (moved to `wss://127.0.0.1:8999/api/chat`, see config change
below) to the plain-PCM/plain-ws tag scheme `backend/app/services/moshi.py`
expects.

**Backend venv did not exist yet.** The brief assumed `backend/.venv/Scripts/python.exe`
was already present; on this machine it was not. The system-wide Python was
3.14, which is too new for this project's pinned `pydantic==2.10.4` (no
prebuilt wheel; `pydantic-core`'s Rust extension fails to build because PyO3
0.22.6 does not support Python 3.14 yet). Installed Python 3.12.10 via
`winget install --id Python.Python.3.12` and created `backend/.venv` with it.
Only the minimal dependency set needed to import `app.services.moshi`
(`pydantic==2.10.4`, `pydantic-settings==2.7.0`, `websockets==14.1`, matching
`backend/requirements.txt`'s pins) plus `PyOgg` (from `ml/moshi/requirements.txt`)
were installed into this venv — not the full `backend/requirements.txt` (heavy
ML deps like chromadb/llama-cpp-python/sentence-transformers are unneeded for
this task and would slow down/risk the install on 3.12 for no benefit here).

**PyOgg's installed API (0.6.14a1) did not match the brief's sketch.** The
brief's draft code assumed `pyogg.OpusEncoder`/`OpusDecoder`/`OggOpusWriter`/
`OggOpusReader` classes. None exist in the installed version — only
`OpusFile`/`OpusFileStream` (path-based, whole-file decode; unusable here since
Kyutai streams a continuous Ogg/Opus bitstream split arbitrarily across
websocket messages, not discrete files) and the raw ctypes bindings in
`pyogg.opus`/`pyogg.ogg` (direct libopus/libogg access). `relay.py` hand-rolls
a real streaming Ogg/Opus muxer (`OpusOggEncoder`) and demuxer
(`OpusOggDecoder`) on top of those raw bindings, one instance per connection.

Three real bugs were found in this PyOgg version's Python-level wrappers and
worked around (see comments in `relay.py`):
1. `ogg_sync_buffer`'s ctypes `restype` is declared `c_char_p`, which makes
   ctypes auto-marshal the return value into an immutable Python `bytes`
   object instead of a raw pointer — `memmove`-ing into it segfaults. Fixed by
   re-declaring `restype = c_void_p` on the underlying `libogg` function.
2. `ogg_stream_pagein`'s and `ogg_stream_packetout`'s Python wrapper functions
   call `libogg.ogg_stream_pagein(oy, og)` / `libogg.ogg_stream_packetout(oy, op)`
   — referencing an out-of-scope variable `oy` instead of the function's own
   `os` parameter (copy-paste bug), raising `NameError` on every call. Worked
   around by calling `ogg.libogg.ogg_stream_pagein`/`packetout` directly.
3. `ogg_sync_clear` has its ctypes signature registered but no Python wrapper
   function defined at all (the intended wrapper was accidentally named
   `oggpack_writeinit`, overwriting three other wrappers). Worked around the
   same way, calling `ogg.libogg.ogg_sync_clear` directly.

**Real-time framing required `ogg_stream_flush`, not `ogg_stream_pageout`.**
An early version paged per outgoing PCM chunk using `ogg_stream_pageout`,
which only emits a page once libogg's internal lacing buffer is "full
enough" — since one 20ms Opus frame compresses to only tens of bytes, this
silently batched roughly a second of audio before producing any output,
which is fatal for a real-time full-duplex protocol and also caused the
Candle server to log `unexpected msg type 242` (a corrupted tag byte from a
stale send race under that bursty pattern). Kyutai's own server
(`stream_both.rs`) calls `ogg::PacketWriter::write_packet(..., EndPage, ...)`
on every packet — i.e. flushes per Opus frame. `relay.py` now calls
`ogg_stream_flush` after every encoded frame to match, and audio flows in
real time in both directions.

**Verification performed live (Steps 4-6 of the brief):**
- Restarted the Candle server on port 8999 (`config-q8.json`'s `"port"` field
  edited from 8998 to 8999 — local gitignored copy, not committed).
  `curl -sk https://localhost:8999/` -> 200.
- Started `ml/moshi/relay.py`; it printed
  `relay listening on ws://127.0.0.1:8998/api/chat`.
- `moshi_client.available(force=True)` -> `True`.
- Sent a continuous ~3s stream of 20ms PCM chunks via `MoshiStream.send_audio`
  in a loop (not the brief's single 100ms burst — see below) and received a
  `MoshiAudio` event back through `stream.events()`, with no
  `MoshiUnavailable` raised. Confirms the full bridge round-trip: PCM -> Ogg/Opus
  -> wss:// upstream -> Candle model -> Ogg/Opus response -> decoded PCM ->
  plain ws:// back to the backend client, tags remapped both directions.
- The brief's literal Step 6 snippet (one 100ms silence burst, then wait for
  one event) does NOT produce an event — Moshi is a full-duplex streaming
  model and needs continuous real-time-paced input before it emits anything;
  a single short burst is consumed by the model with no output yet by the
  time the connection idles out. This is expected behavior given Moshi's
  design, not a relay defect: the same probe with continuous streaming input
  works. `moshi_client.available()` (Step 5) is unaffected either way since it
  only opens and immediately closes the connection.

## Known limitations / deviations from plan
- Plan originally assumed a single `cargo build --features cuda` would work out of the box; three environment-specific fixes were needed (see "Build blockers found and fixed" above). None of these are Blackwell/sm_120-specific — they are generic CUDA-13-vs-cudarc, MSVC-PATH, and CMake-version issues that would recur on any fresh Windows box building this exact dependency tree.
- First-run weight download took ~65 minutes; this is a one-time cost (cached under `%USERPROFILE%\.cache\huggingface\hub\`), not a per-boot cost.
- Candle server now listens on `https://0.0.0.0:8999` (moved off 8998 in Task 5
  so the relay could take 8998) using Kyutai's real protocol (Opus audio, wss
  framing per rust/protocol.md) — NOT the plain ws://+PCM protocol
  `backend/app/services/moshi.py` expects. `ml/moshi/relay.py` (Task 5) bridges
  the two; `settings.moshi_url` (`ws://127.0.0.1:8998/api/chat`) targets the
  relay, not the Candle server directly.
- `backend/.venv` runs Python 3.12, not the system-wide 3.14, because pinned
  `pydantic-core` cannot build on 3.14 yet (PyO3 support pending). Any future
  environment setup on this machine should use 3.12 (or whatever version
  `backend/requirements.txt`'s pins support) explicitly rather than relying on
  the default `python` on PATH.
