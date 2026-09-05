# M1 Moshi Bring-Up — Progress Ledger

Plan: docs/superpowers/plans/2026-09-02-m1-moshi-bringup.md

Task 1: complete (system install, no commit — CUDA 13.3 installed 2026-09-05, verified via nvcc --version)
Task 2: complete (system install + clone, no commit — Rust 1.98.1 installed, Kyutai repo cloned at ml/moshi/candle-moshi, commit e6a55d2722a65870ef52a6c9f6ecfc0e90f38362)

Discovery during Task 2: real Kyutai protocol (rust/protocol.md) does not match backend/app/services/moshi.py's assumed Tag scheme (Opus vs raw PCM, wss vs ws, tag numbering mismatch). Plan file updated in place to reflect: Task 3 now targets the real `moshi-backend` binary + `config-q8.json` + TLS cert; Task 5 rewritten from a conditional "verify protocol" check into a mandatory Opus/PCM + tag-remap relay (ml/moshi/relay.py). User confirmed this approach (relay, not modifying the backend client) on 2026-09-05.

Task 3: complete (driven directly, not via subagent — hands-on GPU build with 3 real blockers hit and fixed: cudarc's CUDA-13 version-detection panic worked around by installing CUDA 12.8 side-by-side and pointing PATH at it for the build; nvcc needed vcvars64.bat sourced for cl.exe; sentencepiece-sys needed CMAKE_POLICY_VERSION_MINIMUM=3.5 for CMake 4.x. moshi-backend.exe built successfully, q8 weights auto-fetched, server confirmed listening on https://0.0.0.0:8998, HTTP 200 verified. Full details in docs/M1_BRINGUP_LOG.md. Server currently left running in background per user request, so Task 5 verification doesn't need a cold restart.)
Task 4: complete (gitignore entries added for ml/moshi/candle-moshi/, weights-q8/, weights-q4/; verified untracked via git status --short)

Base commit before Task 5 dispatch: 37abe6dc6e11181a367445a36fb792a9f6dc2301
