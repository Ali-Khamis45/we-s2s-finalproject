# Moshi serve command (M1 verified working config)

Weights: q8 (kyutai/moshiko-candle-q8), auto-fetched by moshi-backend on first run
to `%USERPROFILE%\.cache\huggingface\hub\`.

Config: `ml/moshi/candle-moshi/rust/moshi-backend/config-q8.json`

Build environment (only needed to rebuild the binary, not to run it — see
`docs/M1_BRINGUP_LOG.md` for why each of these is required):
- CUDA_COMPUTE_CAP=120
- CUDA 12.8 on PATH ahead of any other CUDA install (cudarc 0.16.2 doesn't recognize CUDA 13.x)
- MSVC environment loaded (`vcvars64.bat`) so `nvcc` can find `cl.exe`
- CMAKE_POLICY_VERSION_MINIMUM=3.5 (sentencepiece-sys's vendored CMakeLists needs this on CMake 4.x)

TLS cert (generated once, gitignored): from `ml/moshi/candle-moshi/rust/`:

    MSYS_NO_PATHCONV=1 openssl req -x509 -newkey rsa:4096 -keyout key.pem -out cert.pem -days 365 -nodes -subj "/CN=localhost"

(`MSYS_NO_PATHCONV=1` is required under Git Bash/MSYS — without it, MSYS mangles `/CN=localhost` into a bogus Windows path.)

Command (run from `ml/moshi/candle-moshi/rust/`):

    ./target/release/moshi-backend.exe --config moshi-backend/config-q8.json standalone

Server listens at `https://0.0.0.0:8999` (moved off the default 8998 in M1
so the bridge could own that port — see `config-q8.json`'s `"port"` field) with
TLS, self-signed — this is Kyutai's real protocol: Opus-in-Ogg audio, tag
numbering per `rust/protocol.md`), NOT the plain `ws://` + raw-PCM protocol
`backend/app/services/moshi.py` expects. See `ml/moshi/bridge.py` (M2) for
the adapter that bridges the two — the bridge, listening on
`ws://127.0.0.1:8998/api/chat`, not this server directly, is what
`settings.moshi_url` should point at.

First run took ~65 minutes end-to-end, dominated by the weight download
(one-time cost, cached thereafter). Confirmed reachable via
`curl -sk https://localhost:8999/` → HTTP 200, with the log line
"standalone worker listening on https://0.0.0.0:8999" and the model warmed
up on `Cuda(DeviceId(1))`.
