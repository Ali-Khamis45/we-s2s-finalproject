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

## Known limitations / deviations from plan
- Plan originally assumed a single `cargo build --features cuda` would work out of the box; three environment-specific fixes were needed (see "Build blockers found and fixed" above). None of these are Blackwell/sm_120-specific — they are generic CUDA-13-vs-cudarc, MSVC-PATH, and CMake-version issues that would recur on any fresh Windows box building this exact dependency tree.
- First-run weight download took ~65 minutes; this is a one-time cost (cached under `%USERPROFILE%\.cache\huggingface\hub\`), not a per-boot cost.
- Server listens on `https://0.0.0.0:8998` using Kyutai's real protocol (Opus audio, wss framing per rust/protocol.md) — NOT the plain ws://+PCM protocol `backend/app/services/moshi.py` expects. The relay (Task 5) is required before the backend can talk to this server; the raw Candle server is not directly usable as `settings.moshi_url`'s target.
