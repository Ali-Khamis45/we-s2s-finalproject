# M1: Moshi on RTX 5050 — Bring-Up Implementation Plan

> **STATUS: ✅ COMPLETE (2026-09-05).** All 7 tasks implemented and reviewed clean (Task 6 needed one fix-and-re-review cycle; all others passed first review), plus a final whole-branch review (one round of doc fixes applied after). See `docs/M1_BRINGUP_LOG.md` for the full record and `docs/PROJECT_PLAN.md`'s M2 entry for the handoff to the next task. This file is kept as historical record of what was actually built (several sections were corrected in place as real facts emerged during execution — read the "Confirmed protocol mismatch" note and each task's "COMPLETE" annotation for what changed and why).

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. This plan is hardware bring-up, not TDD application code — "tests" here are verification commands against real hardware/model output, not unit tests.

**Goal:** Get quantized Moshi running locally on the RTX 5050 (Blackwell, sm_120, 8GB VRAM), serving audio in/out plus the Inner Monologue text stream over the exact WebSocket wire protocol `backend/app/services/moshi.py` already expects, with measured p50/p95 time-to-first-audio latency recorded for the M12 comparison.

**Architecture:** Native Windows toolchain (no WSL). Install CUDA 12.8+ and Rust, build Kyutai's Rust/Candle Moshi implementation (`moshi-backend` binary) for sm_120, download q4 (fallback q8) quantized weights, and run a Python relay in front of it that translates between Kyutai's real wire protocol (Opus-in-Ogg audio, `wss://` TLS, tag numbering per `rust/protocol.md`) and the tag-byte protocol `backend/app/services/moshi.py` already speaks (raw PCM, plain `ws://`). Benchmark the relay end-to-end with the same p50/p95 methodology `backend/scripts/bench_latency.py` uses for the cascade.

**Confirmed protocol mismatch (found during Task 2, 2026-09-05):** Cloning Kyutai's repo and reading `ml/moshi/candle-moshi/rust/protocol.md` showed the real `moshi-backend` server protocol differs from what `backend/app/services/moshi.py` assumes:
- Audio (tag 1) payload is **Ogg/Opus-encoded**, not raw 16-bit PCM.
- Error is tag `0x05` in Kyutai's protocol, not `0x04` as the backend's `Tag` enum has it.
- Kyutai's protocol has an additional `MetaData` tag (`0x04`, JSON payload) the backend enum doesn't model.
- Handshake (tag 0) carries a payload (`u32` protocol version + `u32` model version); the backend assumes no payload.
- Control (tag 3) sub-codes are Start/EndTurn/Pause/Restart (1 byte); "not used in full streaming mode" per Kyutai's docs, so this one is likely moot for our full-duplex path.
- The server defaults to **`wss://` (TLS, self-signed cert)**, not the plain `ws://` the backend client connects with.

Decision (confirmed with the user 2026-09-05): do **not** modify `backend/app/services/moshi.py`. Build a Python relay instead (this absorbed what was previously "Task 5, Step 2 — only if needed"; it is now unconditional — see Task 5). The relay owns: Opus encode/decode (raw PCM ⇄ Ogg/Opus), tag renumbering, TLS termination (relay speaks plain `ws://` to the backend, `wss://` to Kyutai's server), and dropping/ignoring MetaData and Control frames the backend doesn't model.

**Tech Stack:** CUDA 12.8+ (13.3 confirmed installed 2026-09-05), Rust/Cargo (1.98.1 confirmed installed 2026-09-05), Kyutai `moshi` Rust/Candle crate (`moshi-backend` binary, `kyutai/moshiko-candle-q8` weights), Python `websockets` + `PyOgg` for the protocol relay and bench client, PowerShell for setup scripts.

## Global Constraints

- Target GPU: RTX 5050, 8GB VRAM, Blackwell `sm_120`. Requires CUDA 12.8+ and a CUDA-enabled PyTorch/Candle build targeting `sm_120` — do not use `bitsandbytes` on this box (already forbidden repo-wide per `ml/requirements.txt`, which is scoped to the Colab/Kaggle training env only).
- `moshi.cpp` does not exist — do not search for or reference it. The real quantized path is Kyutai's Rust/Candle implementation.
- Wire protocol is **frozen** by `backend/app/services/moshi.py` — do not renegotiate framing. One leading tag byte + payload:
  - `0x00` HANDSHAKE (no payload)
  - `0x01` AUDIO (raw 16-bit PCM payload)
  - `0x02` TEXT (UTF-8 Inner Monologue delta)
  - `0x03` CONTROL (no payload)
  - `0x04` ERROR (UTF-8 detail payload)
  - Full-duplex: server must accept `AUDIO` frames continuously while also emitting `AUDIO`/`TEXT` frames — no turn-taking, no request/response pairing.
- Default listen target: `ws://127.0.0.1:8998/api/chat` (`backend/app/core/config.py:42`, `settings.moshi_url`) — owned by the relay (Task 5), not by Kyutai's Candle server directly, since the two speak different protocols. Sample rate: `24_000` Hz, 16-bit PCM (`settings.moshi_sample_rate`). Connect timeout the client honors: `settings.moshi_connect_timeout_s = 3.0`.
- Timebox: **2 days**. q8 is the primary target (no q4 config ships upstream — see Task 3). If q8 fails on GPU, fall back to CPU Candle inference (degraded but demonstrable — set `--features cuda` off, or use whatever CPU feature flag the crate exposes). If that also fails, escalate immediately — do not silently keep debugging past the timebox; the cascade becomes primary per the plan doc's risk table.
- All new setup/bench scripts go in `ml/moshi/` (Rust/model side) and get `scripts/make.ps1` + `Makefile` targets, following the existing per-verb pattern (`Invoke-Backend`-style helpers, `## comment` in `help`).
- Never install CUDA/Rust/model weights silently — every install step in this plan is a visible, logged command the user can see run.

---

## File Structure

```
ml/moshi/
  .gitkeep                  # already exists, stays
  candle-moshi/              # third-party clone of Kyutai's Rust/Candle Moshi repo, gitignored (Task 2)
  requirements.txt           # websockets + PyOgg for the relay/bench scripts (Task 5)
  relay.py                   # protocol bridge: Kyutai's real wss://+Opus <-> our ws://+PCM (Task 5)
  bench_moshi_latency.py     # p50/p95 harness, mirrors backend/scripts/bench_latency.py (Task 6)
  serve_moshi.md             # notes: exact Candle serve command, config, port (Task 3)
docs/
  M1_BRINGUP_LOG.md          # dated log of what was tried, what worked/failed, final config (Task 1, finalized Task 7)
scripts/make.ps1             # add "moshi-serve", "moshi-bench" targets (Task 7)
Makefile                     # mirror the same targets (Task 7)
```

No files under `backend/` or `frontend/` change — `backend/app/services/moshi.py` is consumed exactly as committed, with zero modifications. M1's deliverable is the model service plus the relay that lets that unmodified client talk to it; M2 (Track M's production-grade version of this bridge, integrated into their own service) is a separate, later task that can build on `ml/moshi/relay.py` as a reference or replace it outright.

---

## Task 1: Verify driver and install CUDA 12.8+ — COMPLETE (2026-09-05)

**Files:** none (system-level install)

- [x] **Step 1: Confirm current driver supports CUDA 12.8+**

Done: driver 610.74, CUDA UMD 13.3 reported by `nvidia-smi` — well above 12.8+.

- [x] **Step 2: Install CUDA Toolkit 12.8+ if `nvcc` is missing**

Done: `nvcc` was missing; user downloaded and ran the CUDA Toolkit installer (Custom install, display driver component unchecked to avoid downgrading the existing 610.74 driver).

- [x] **Step 3: Verify `nvcc` and confirm sm_120 target is buildable**

Done: `nvcc --version` reports release 13.3, V13.3.73.

- [x] **Step 4: Log the result**

Done: recorded in `docs/M1_BRINGUP_LOG.md`.

---

## Task 2: Install Rust and fetch Kyutai's Candle Moshi implementation — COMPLETE (2026-09-05)

**Files:**
- Create: `ml/moshi/candle-moshi/` (cloned repo)

- [x] **Step 1: Install Rust via rustup**

Done: installed via `rustup-init.exe` (stable-x86_64-pc-windows-msvc), rustc 1.98.1.

- [x] **Step 2: Verify Rust toolchain**

Done: `rustc --version` → 1.98.1, `cargo --version` → 1.98.1.

- [x] **Step 3: Clone Kyutai's Moshi Rust/Candle implementation**

Done: `git clone https://github.com/kyutai-labs/moshi.git ml/moshi/candle-moshi`.

- [x] **Step 4: Log the clone commit hash**

Done: commit `e6a55d2722a65870ef52a6c9f6ecfc0e90f38362` — record this in `docs/M1_BRINGUP_LOG.md` in Task 7's finalization pass (not yet written to the log file as of this edit).

**Discovery made during this task (see "Confirmed protocol mismatch" under Architecture above):** reading `rust/protocol.md` and `rust/moshi-backend/config*.json` revealed the real server protocol and serve command differ substantially from what was assumed when this plan was first drafted. Task 3 and Task 5 below were rewritten to reflect the real `moshi-backend` binary, `config-q8.json`, TLS requirement, and Opus audio encoding — this is not the original plan text, it is the corrected version.

---

## Task 3: Build Candle Moshi for CUDA/sm_120 and download q8 weights — COMPLETE (2026-09-05)

**Files:**
- Create: `ml/moshi/serve_moshi.md`

- [ ] **Step 1: Build the Rust CUDA binary**

From `ml/moshi/candle-moshi/rust/` (the Rust workspace root in Kyutai's repo layout), run:
```powershell
cd ml/moshi/candle-moshi/rust
cargo build --release --features cuda
```
Expected: a successful build producing a `target/release/moshi-*.exe` binary (or the crate's documented entry point — check the repo's README for the exact binary name, which may differ from a guess). This step commonly fails first on Blackwell — if it does, capture the full error text before troubleshooting.

- [ ] **Step 2: If the CUDA build fails on sm_120, check Candle's CUDA arch list**

Blackwell (`sm_120`) is new; Candle's `candle-kernels` crate may need an explicit arch override. Check for a `CUDA_COMPUTE_CAP` or similar env var documented in the Candle repo (`candle-core`'s build.rs) and set it explicitly:
```powershell
$env:CUDA_COMPUTE_CAP = "120"
cargo build --release --features cuda
```
If this still fails, this is the trigger condition for the **q8 fallback** — do not spend more than a few hours on q4-specific build issues before moving to Step 3 with q8 weights, since q8 is the documented fallback in the plan doc's risk table.

**Confirmed from `ml/moshi/candle-moshi/rust/README.md` and `rust/moshi-backend/config*.json` (read during Task 2):**
- The server binary is `moshi-backend`, run via `cargo run --features cuda --bin moshi-backend -r -- --config <config-file> standalone` from the `rust/` directory — not a standalone `.exe` to hunt for by name.
- Config selects the weight variant: `moshi-backend/config.json` uses `hf_repo: "kyutai/moshiko-candle-bf16"` (full precision); `moshi-backend/config-q8.json` uses `hf_repo: "kyutai/moshiko-candle-q8"`. There is no q4 config shipped by default — q8 is the realistic first target, not q4. If a q4 config/repo turns out to exist upstream, treat it as a bonus, not the default plan.
- Weights are **fetched automatically by the server from the configured `hf_repo`** on first run (standard Candle/HF pattern) — no separate `huggingface-cli download` step is needed unless the automatic fetch fails, in which case download `kyutai/moshiko-candle-q8` manually and point `lm_model_file`/`mimi_model_file` at the local paths.
- The server **requires TLS** (`wss://`) and looks for `key.pem`/`cert.pem` in `cert_dir` (`.` by default, i.e. the `rust/` working directory). Generate a self-signed pair before first run:
  ```bash
  openssl req -x509 -newkey rsa:4096 -keyout key.pem -out cert.pem -days 365 -nodes -subj "/CN=localhost"
  ```
- Default port is `8998` (matches `settings.moshi_url`'s port), default bind address `0.0.0.0`.

- [ ] **Step 3: Generate a local TLS cert and start the server with the q8 config**

From `ml/moshi/candle-moshi/rust/`:
```powershell
openssl req -x509 -newkey rsa:4096 -keyout key.pem -out cert.pem -days 365 -nodes -subj "/CN=localhost"
$env:CUDA_COMPUTE_CAP = "120"
cargo run --features cuda --bin moshi-backend -r -- --config moshi-backend/config-q8.json standalone
```
Expected: the server downloads weights from `kyutai/moshiko-candle-q8` on first run (this can take a while — multi-GB), then prints `standalone worker listening`. This is the trigger to move to Step 4. If CUDA build errors appear here instead, this is the `CUDA_COMPUTE_CAP` / sm_120 troubleshooting case from Step 2.

- [ ] **Step 4: Confirm the server answers on `wss://localhost:8998`**

With the server running, open `https://localhost:8998` in a browser (Kyutai's own web UI, bundled at `../client/dist`) and confirm the page loads (accept the self-signed cert warning). This proves the binary, weights, and TLS cert are all correctly wired before building anything on top of it. Do not proceed to Task 5 until this loads.

- [ ] **Step 5: Write `ml/moshi/serve_moshi.md`**

Document the exact working command line and the weight variant actually used (fill in from what Steps 3-4 actually produced — do not leave placeholders):
```markdown
# Moshi serve command (M1 verified working config)

Weights: q8 (kyutai/moshiko-candle-q8), auto-fetched by moshi-backend on first run
Config: ml/moshi/candle-moshi/rust/moshi-backend/config-q8.json
CUDA_COMPUTE_CAP: 120
TLS cert: ml/moshi/candle-moshi/rust/{key.pem,cert.pem} (self-signed, generated locally, gitignored)

Command (run from ml/moshi/candle-moshi/rust/):
    $env:CUDA_COMPUTE_CAP = "120"
    cargo run --features cuda --bin moshi-backend -r -- --config moshi-backend/config-q8.json standalone

Server listens at wss://localhost:8998 (TLS, self-signed — Kyutai's real protocol,
NOT the plain ws:// backend/app/services/moshi.py expects). See ml/moshi/relay.py
and Task 5 for the adapter that bridges the two.
```

- [ ] **Step 6: Commit the setup notes (not the weights, cert, or clone)**

```bash
git add ml/moshi/serve_moshi.md
git commit -m "docs: record working Moshi Candle build config for RTX 5050"
```

---

## Task 4: Add `.gitignore` entries for the clone and weights — COMPLETE (2026-09-05)

**Files:**
- Modify: `.gitignore`

- [ ] **Step 1: Check current `.gitignore` for existing patterns**

Run:
```bash
grep -n "ml/" .gitignore
```

- [ ] **Step 2: Add entries**

Add to `.gitignore`:
```
# M1: third-party Candle clone, downloaded weights, and local dev TLS cert
ml/moshi/candle-moshi/
ml/moshi/weights-q8/
ml/moshi/weights-q4/
```
(The clone's own `.gitignore` should already cover `*.pem` inside `ml/moshi/candle-moshi/`, but since that whole directory is ignored by the pattern above, the cert files are covered regardless of where within it they land.)

- [ ] **Step 3: Verify they're ignored**

Run:
```bash
git status --short ml/moshi/
```
Expected: no untracked entries for `candle-moshi/` or `weights-*/` (only `serve_moshi.md` and later files show up).

- [ ] **Step 4: Commit**

```bash
git add .gitignore
git commit -m "chore: ignore third-party Candle clone and Moshi weights"
```

---

## Task 5: Build the protocol relay (Opus↔PCM, tag remap, TLS↔plain) — COMPLETE (2026-09-05, review approved)

**Files:**
- Create: `ml/moshi/relay.py`
- Modify: `ml/moshi/candle-moshi/rust/moshi-backend/config-q8.json` (port only, local working copy — this file lives inside the gitignored clone, so this "modify" does not need a commit)

**Context:** Task 2's exploration confirmed Kyutai's real server protocol (`rust/protocol.md`) does not match `backend/app/services/moshi.py`'s `Tag` enum — see the plan's "Confirmed protocol mismatch" note under Architecture. This task builds the relay that bridges them. It is a permanent part of this project's Moshi integration (not a throwaway verification shim), since M2 ("Moshi streaming service" — Track M) will build on top of whatever this task proves works.

**Interfaces:**
- Consumes: Kyutai's real protocol server at `wss://127.0.0.1:8999/api/chat` (moved off 8998 — see Step 1) — tags per `rust/protocol.md` (0=Handshake, 1=Audio/Opus-in-Ogg, 2=Text, 3=Control, 4=MetaData, 5=Error, 6=Ping).
- Produces: a plain `ws://127.0.0.1:8998/api/chat` endpoint matching `backend/app/services/moshi.py`'s `Tag` enum exactly (0x00 HANDSHAKE no-payload, 0x01 AUDIO raw 16-bit PCM, 0x02 TEXT UTF-8, 0x03 CONTROL no-payload, 0x04 ERROR UTF-8 detail) — this is what `moshi_client` (already committed, unmodified) connects to.

- [ ] **Step 1: Move Kyutai's server off port 8998**

The relay must own port 8998 (what `settings.moshi_url` points at), so Kyutai's real server needs a different port. Edit the local (gitignored) config copy:
```powershell
# ml/moshi/candle-moshi/rust/moshi-backend/config-q8.json
# change "port": 8998  →  "port": 8999
```
Restart the server from Task 3 with this config; confirm `https://localhost:8999` now loads instead of 8998.

- [ ] **Step 2: Install Opus codec support for Python**

The relay needs to encode outgoing PCM to Ogg/Opus and decode incoming Ogg/Opus to PCM. Add to `backend/requirements.txt`... **no** — this is a `ml/moshi/`-local tool, not a backend dependency. Create `ml/moshi/requirements.txt`:
```
websockets>=12.0
PyOgg>=0.6.14a1
```
Install into the same backend venv (simplest — avoids a second venv for one script) or a fresh one; either way, record which in `docs/M1_BRINGUP_LOG.md`:
```powershell
backend\.venv\Scripts\python.exe -m pip install -r ml\moshi\requirements.txt
```

- [ ] **Step 3: Write `ml/moshi/relay.py`**

```python
"""Protocol relay bridging Kyutai's real moshi-backend wire protocol
(rust/protocol.md: Opus-in-Ogg audio, wss://, tags 0-6) to the plain-PCM,
plain-ws:// tag scheme backend/app/services/moshi.py already speaks.

This is a permanent part of the integration, not a throwaway shim — M2
(Track M) builds the production version of this bridge; this one exists
to prove the bridge is possible and to unblock M1's latency measurement.
"""
from __future__ import annotations

import asyncio
import ssl
import struct

import websockets
from pyogg import OpusDecoder, OpusEncoder, OggOpusWriter, OggOpusReader
import io

UPSTREAM_URL = "wss://127.0.0.1:8999/api/chat"
LISTEN_HOST = "127.0.0.1"
LISTEN_PORT = 8998

# Kyutai's real tags (rust/protocol.md)
KT_HANDSHAKE, KT_AUDIO, KT_TEXT, KT_CONTROL, KT_METADATA, KT_ERROR, KT_PING = range(7)

# backend/app/services/moshi.py's Tag enum
OUR_HANDSHAKE, OUR_AUDIO, OUR_TEXT, OUR_CONTROL, OUR_ERROR = range(5)

SAMPLE_RATE = 24_000
CHANNELS = 1


def pcm_to_ogg_opus(pcm: bytes) -> bytes:
    """Encode raw 16-bit PCM into an Ogg/Opus frame for upstream."""
    buf = io.BytesIO()
    writer = OggOpusWriter(buf)
    writer.set_sample_rate(SAMPLE_RATE)
    writer.set_channels(CHANNELS)
    writer.write(pcm)
    writer.close()
    return buf.getvalue()


def ogg_opus_to_pcm(ogg_bytes: bytes) -> bytes:
    """Decode an incoming Ogg/Opus frame from upstream into raw 16-bit PCM."""
    reader = OggOpusReader(io.BytesIO(ogg_bytes))
    out = bytearray()
    while True:
        chunk = reader.get_buffered_samples()
        if not chunk:
            break
        out.extend(chunk)
    return bytes(out)


async def bridge_upstream_to_client(upstream, client) -> None:
    async for frame in upstream:
        if not frame:
            continue
        tag, payload = frame[0], frame[1:]
        if tag == KT_AUDIO:
            pcm = ogg_opus_to_pcm(payload)
            if pcm:
                await client.send(bytes([OUR_AUDIO]) + pcm)
        elif tag == KT_TEXT:
            await client.send(bytes([OUR_TEXT]) + payload)
        elif tag == KT_ERROR:
            await client.send(bytes([OUR_ERROR]) + payload)
        # KT_HANDSHAKE, KT_CONTROL, KT_METADATA, KT_PING: no equivalent
        # our client models: drop rather than guess at a mapping.


async def bridge_client_to_upstream(client, upstream) -> None:
    async for frame in client:
        if not frame:
            continue
        tag, payload = frame[0], frame[1:]
        if tag == OUR_AUDIO:
            ogg = pcm_to_ogg_opus(payload)
            await upstream.send(bytes([KT_AUDIO]) + ogg)
        # OUR_HANDSHAKE/OUR_CONTROL carry no payload our client sends today;
        # extend here if a later task starts sending them.


async def handler(client) -> None:
    ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE  # local self-signed dev cert only
    async with websockets.connect(UPSTREAM_URL, ssl=ssl_ctx, max_size=None) as upstream:
        await asyncio.gather(
            bridge_upstream_to_client(upstream, client),
            bridge_client_to_upstream(client, upstream),
        )


async def main() -> None:
    async with websockets.serve(handler, LISTEN_HOST, LISTEN_PORT, max_size=None):
        print(f"relay listening on ws://{LISTEN_HOST}:{LISTEN_PORT}/api/chat")
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
```

**Note on the Opus codec calls above:** `PyOgg`'s `OggOpusWriter`/`OggOpusReader` API surface varies by version — if `write()`/`get_buffered_samples()` don't match the installed version's actual method names, check `python -c "import pyogg; help(pyogg.OggOpusWriter)"` and adjust the two codec functions accordingly. The tag-remapping logic in `bridge_upstream_to_client`/`bridge_client_to_upstream` is the part that must not change; the codec calls are the part likely to need adjustment for the exact installed version.

- [ ] **Step 4: Start the full chain and confirm the relay listens**

Terminal 1 (Kyutai server, from Task 3 with the port-9 config from Step 1):
```powershell
cd ml\moshi\candle-moshi\rust
$env:CUDA_COMPUTE_CAP = "120"
cargo run --features cuda --bin moshi-backend -r -- --config moshi-backend/config-q8.json standalone
```
Terminal 2 (relay):
```powershell
backend\.venv\Scripts\python.exe ml\moshi\relay.py
```
Expected: relay prints `relay listening on ws://127.0.0.1:8998/api/chat`.

- [ ] **Step 5: Verify with the backend's own client against the relay**

From the `backend/` venv, run a short Python probe using the already-committed client:
```powershell
cd backend
.venv\Scripts\python.exe -c "
import asyncio
from app.services.moshi import moshi_client

async def main():
    ok = await moshi_client.available(force=True)
    print('available:', ok)

asyncio.run(main())
"
```
Expected: `available: True`. This is the real integration check — it proves the server satisfies exactly what `backend/app/services/moshi.py` expects, with zero changes to that file.

- [ ] **Step 6: Send one real audio chunk and confirm an event comes back**

Extend the probe to open a session and push a short silence/tone PCM buffer, confirming `MoshiAudio` or `MoshiText` events arrive:
```powershell
.venv\Scripts\python.exe -c "
import asyncio
from app.services.moshi import moshi_client, MoshiAudio, MoshiText

async def main():
    async with moshi_client.session() as stream:
        silence = b'\x00\x00' * 2400  # 100ms of 16-bit silence at 24kHz
        await stream.send_audio(silence)
        async for event in stream.events():
            print(type(event).__name__)
            break

asyncio.run(main())
"
```
Expected: prints `MoshiAudio` or `MoshiText` without raising `MoshiUnavailable`. If nothing arrives, check the relay's terminal output for exceptions first — a codec mismatch (Step 3's note) is the most likely cause.

- [ ] **Step 7: Commit the relay**

```bash
git add ml/moshi/relay.py ml/moshi/requirements.txt
git commit -m "feat: add Opus/PCM protocol relay bridging Kyutai's moshi-backend to backend/app/services/moshi.py"
```

---

## Task 6: Measure and record latency (p50/p95) — COMPLETE (2026-09-05, review approved after 1 fix cycle)

**Measured result: p50=1600.8ms, p95=1775.9ms, n=10.** See `docs/M1_BRINGUP_LOG.md` for the full writeup including root-cause findings and the silence-not-speech deviation.

**Files:**
- Create: `ml/moshi/bench_moshi_latency.py`

**Interfaces:**
- Consumes: `app.services.moshi.moshi_client`, `MoshiAudio`, `MoshiText` from `backend/app/services/moshi.py` (already built, no changes).
- Produces: a p50/p95 "time to first audio" report in the same units/format as `backend/scripts/bench_latency.py`, so M12's Moshi-vs-cascade comparison can read both directly.

- [ ] **Step 1: Write the benchmark script**

Create `ml/moshi/bench_moshi_latency.py`, mirroring `backend/scripts/bench_latency.py`'s `report(name, samples, unit="ms")` helper (median via `statistics.median`, p95 via `sorted(samples)[int(len(samples)*0.95)]`), run against the live server from Task 5:

```python
"""Moshi Candle server latency: time-to-first-audio, p50/p95.

Mirrors backend/scripts/bench_latency.py's reporting format so the
numbers are directly comparable for M12 (Moshi vs cascade).

Usage: python ml/moshi/bench_moshi_latency.py <wav_path> <N>
"""
from __future__ import annotations

import asyncio
import statistics
import sys
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from app.services.moshi import moshi_client, MoshiAudio  # noqa: E402


def report(name: str, samples: list[float], unit: str = "ms") -> None:
    p50 = statistics.median(samples)
    p95 = sorted(samples)[int(len(samples) * 0.95)] if len(samples) > 1 else samples[0]
    print(f"{name}: p50={p50:.1f}{unit} p95={p95:.1f}{unit} n={len(samples)}")


def load_pcm(wav_path: str) -> bytes:
    with wave.open(wav_path, "rb") as wf:
        assert wf.getframerate() == 24_000, "expects 24kHz PCM to match moshi_sample_rate"
        return wf.readframes(wf.getnframes())


async def one_run(pcm: bytes) -> float:
    start = time.perf_counter()
    async with moshi_client.session() as stream:
        await stream.send_audio(pcm)
        async for event in stream.events():
            if isinstance(event, MoshiAudio):
                return (time.perf_counter() - start) * 1000
    raise RuntimeError("no audio event received")


async def main(wav_path: str, n: int) -> None:
    pcm = load_pcm(wav_path)

    ok = await moshi_client.available(force=True)
    if not ok:
        raise SystemExit("Moshi server not reachable at settings.moshi_url")

    # Warm run, excluded from the reported samples.
    await one_run(pcm)

    samples = [await one_run(pcm) for _ in range(n)]
    report("time to first audio", samples)

    print()
    print("READ THIS BEFORE QUOTING THE NUMBERS")
    print("These numbers are the Moshi side of the M12 comparison against")
    print("backend/scripts/bench_latency.py's cascade figures. Same machine,")
    print("same warm-path assumption: first run excluded as a warm-up.")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], int(sys.argv[2])))
```

- [ ] **Step 2: Run it against a short test utterance**

Reuse an existing test WAV from the cascade bench fixtures if one is 24kHz mono, or record/convert one:
```powershell
cd backend
.venv\Scripts\python.exe ..\ml\moshi\bench_moshi_latency.py scripts\words\dysfluent_utterance.wav 10
```
Expected: prints `time to first audio: p50=<N>ms p95=<N>ms n=10`, expected to land near the ~200ms figure the plan doc states as Moshi's target — if it's dramatically higher, note that as a finding rather than silently re-running until it looks better.

- [ ] **Step 3: Record the measured numbers**

Append the exact output to `docs/M1_BRINGUP_LOG.md` under a "Measured latency" heading, with timestamp, GPU state (idle/warm), and which weight variant (q4/q8) was used.

- [ ] **Step 4: Commit**

```bash
git add ml/moshi/bench_moshi_latency.py docs/M1_BRINGUP_LOG.md
git commit -m "feat: add Moshi Candle latency benchmark (M1 measured result)"
```

---

## Task 7: Wire up task-runner targets and finalize the bring-up log

**Files:**
- Modify: `scripts/make.ps1`
- Modify: `Makefile`
- Create: `docs/M1_BRINGUP_LOG.md` (if not already created incrementally in earlier tasks — consolidate here)

- [ ] **Step 1: Add `moshi-serve` and `moshi-bench` targets to `scripts/make.ps1`**

Edit the `ValidateSet` list (line 12-13) to add `"moshi-serve", "moshi-bench"`, and add corresponding `switch` cases following the existing pattern:

Following the `dev` target's pattern (line 58-71: `Start-Process` for the background half, foreground for the one you watch):

```powershell
    "moshi-serve" {
        Write-Host "candle server https://localhost:8999 (internal, Kyutai's real protocol)"
        Write-Host "relay          ws://127.0.0.1:8998/api/chat (what backend/app/services/moshi.py talks to)"
        $env:CUDA_COMPUTE_CAP = "120"
        $rustDir = Join-Path $Repo "ml\moshi\candle-moshi\rust"
        $candle = Start-Process -FilePath "cargo" `
            -ArgumentList "run", "--features", "cuda", "--bin", "moshi-backend", "-r", "--", `
                          "--config", "moshi-backend/config-q8.json", "standalone" `
            -WorkingDirectory $rustDir -PassThru -NoNewWindow
        try {
            & $Py (Join-Path $Repo "ml\moshi\relay.py")
        } finally {
            Stop-Process -Id $candle.Id -Force -ErrorAction SilentlyContinue
        }
    }

    "moshi-bench" {
        Invoke-Backend @("..\ml\moshi\bench_moshi_latency.py", "scripts\words\dysfluent_utterance.wav", "10")
    }
```

Update the `"help"` block (lines 36-49) to add two lines:
```
  moshi-serve    Start Kyutai's Candle server + the protocol relay (needs GPU + weights)
  moshi-bench    Measure Moshi time-to-first-audio p50/p95 (needs moshi-serve running)
```

- [ ] **Step 2: Mirror the same two targets in `Makefile`**

Read the `Makefile`'s existing `bench`/`bench-fast` target style and add `moshi-serve`/`moshi-bench` targets calling the same underlying commands (adjusted for POSIX-style invocation, matching whatever convention the existing `bench` target uses there).

- [ ] **Step 3: Run `moshi-bench` end-to-end via the task runner**

```powershell
.\scripts\make.ps1 moshi-serve
```
(in one terminal, left running), then in another:
```powershell
.\scripts\make.ps1 moshi-bench
```
Expected: same p50/p95 output as Task 6 Step 2, now reachable via the standard task-runner entry point.

- [ ] **Step 4: Finalize `docs/M1_BRINGUP_LOG.md`**

Ensure it reads as a coherent record with these sections, using the actual values gathered in Tasks 1-6 (no placeholders):
```markdown
# M1 Bring-Up Log — Moshi on RTX 5050

## Environment
- Driver: <version>, CUDA: <version>
- Rust: <version>
- Kyutai moshi repo commit: <hash>

## Weight variant used
q8 (kyutai/moshiko-candle-q8) — no q4 config ships upstream; bf16 (config.json) was the only other option and was not used (VRAM budget)

## Protocol relay
ml/moshi/relay.py bridges Kyutai's real protocol (wss://, Opus-in-Ogg, rust/protocol.md tag numbering)
to the plain ws://, raw-PCM tag scheme backend/app/services/moshi.py expects. See the plan's
"Confirmed protocol mismatch" note for why this exists.

## Working serve command
See ml/moshi/serve_moshi.md (Candle server) and Task 5 (relay)

## Measured latency (time to first audio)
p50: <N>ms, p95: <N>ms, n=<N>, warm GPU

## Known limitations / deviations from plan
(e.g. if CPU fallback was needed, or q4 never worked)
```

- [ ] **Step 5: Commit**

```bash
git add scripts/make.ps1 Makefile docs/M1_BRINGUP_LOG.md
git commit -m "feat: add moshi-serve/moshi-bench task runner targets, finalize M1 bring-up log"
```

---

## Verification

1. `nvcc --version` and `cargo --version` both succeed (Task 1-2). **Done 2026-09-05**: CUDA 13.3, Rust 1.98.1.
2. `ml/moshi/candle-moshi/rust/target/release/moshi-backend.exe` (or the cargo-run equivalent) exists and was built with `--features cuda` (Task 3).
3. `.\scripts\make.ps1 moshi-serve` starts Kyutai's Candle server on `127.0.0.1:8999` and the relay on `127.0.0.1:8998` (Task 7).
4. With that running, `moshi_client.available(force=True)` returns `True` from the backend's own client code, unmodified (Task 5 Step 5).
5. A real PCM chunk sent through `MoshiStream.send_audio` produces at least one `MoshiAudio` or `MoshiText` event back, round-tripped through the relay's Opus↔PCM conversion (Task 5 Step 6).
6. `.\scripts\make.ps1 moshi-bench` prints a p50/p95 time-to-first-audio figure, recorded in `docs/M1_BRINGUP_LOG.md` (Task 6-7).
7. If any fallback tier was used (CPU instead of GPU, or bf16 instead of q8), `docs/M1_BRINGUP_LOG.md`'s "Known limitations" section says so explicitly — this is the artifact M2 and the report (S2) will read to know what they're building on.
8. The relay's tag-mapping and codec logic in `ml/moshi/relay.py` matches the "Confirmed protocol mismatch" note under Architecture exactly — no renumbering drift between what's documented and what's implemented.
