# M1 Moshi Bring-Up — Progress Ledger

Plan: docs/superpowers/plans/2026-09-02-m1-moshi-bringup.md

Task 1: complete (system install, no commit — CUDA 13.3 installed 2026-09-05, verified via nvcc --version)
Task 2: complete (system install + clone, no commit — Rust 1.98.1 installed, Kyutai repo cloned at ml/moshi/candle-moshi, commit e6a55d2722a65870ef52a6c9f6ecfc0e90f38362)

Discovery during Task 2: real Kyutai protocol (rust/protocol.md) does not match backend/app/services/moshi.py's assumed Tag scheme (Opus vs raw PCM, wss vs ws, tag numbering mismatch). Plan file updated in place to reflect: Task 3 now targets the real `moshi-backend` binary + `config-q8.json` + TLS cert; Task 5 rewritten from a conditional "verify protocol" check into a mandatory Opus/PCM + tag-remap relay (ml/moshi/relay.py). User confirmed this approach (relay, not modifying the backend client) on 2026-09-05.

Task 3: complete (driven directly, not via subagent — hands-on GPU build with 3 real blockers hit and fixed: cudarc's CUDA-13 version-detection panic worked around by installing CUDA 12.8 side-by-side and pointing PATH at it for the build; nvcc needed vcvars64.bat sourced for cl.exe; sentencepiece-sys needed CMAKE_POLICY_VERSION_MINIMUM=3.5 for CMake 4.x. moshi-backend.exe built successfully, q8 weights auto-fetched, server confirmed listening on https://0.0.0.0:8998, HTTP 200 verified. Full details in docs/M1_BRINGUP_LOG.md. Server currently left running in background per user request, so Task 5 verification doesn't need a cold restart.)
Task 4: complete (gitignore entries added for ml/moshi/candle-moshi/, weights-q8/, weights-q4/; verified untracked via git status --short)

Task 5: complete (commits c879605..e73c897, review clean — Approved, no Critical/blocking Important findings). Relay built at ml/moshi/relay.py, listens ws://127.0.0.1:8998/api/chat, dials wss://127.0.0.1:8999/api/chat upstream. Verified live: moshi_client.available(force=True) -> True, continuous PCM stream -> MoshiAudio event, zero changes to backend/app/services/moshi.py. Found and fixed 3 real PyOgg ctypes bugs + 1 self-introduced framing bug (ogg_stream_flush vs pageout). backend/.venv now runs Python 3.12 (system default 3.14 can't build pydantic-core). Non-blocking follow-up noted by reviewer: relay has no OUR_ERROR translation when the upstream Candle connection itself fails (client sees abrupt close, no diagnostic frame) — worth a fix in M2's production version, not required for M1.
Candle server currently running on port 8999, relay on port 8998 (both left up per implementer's note).

Task 6: complete (commits 0a79927, 9e6d58a — fix commit after 1 review cycle, re-review clean/Approved). ml/moshi/bench_moshi_latency.py measures Moshi time-to-first-audio through the live relay+Candle stack using the real unmodified moshi_client. MEASURED RESULT (M1 headline number for M12): p50=1600.8ms p95=1775.9ms n=10, all attempts=1 (zero retries), warm GPU, q8 weights. This is ~8x the plan's ~200ms target for Moshi alone — attributed mostly to Task 5's throwaway relay overhead (confirmed bugs: 20ms chunks desync the Candle server's 40ms flush quantum, relay segfaults on teardown, relay's Opus encoder crashes on real speech content). Benchmark streams silence not real speech as a documented, reviewed-acceptable deviation. All of this is written up in docs/M1_BRINGUP_LOG.md. M2 should re-run this exact benchmark once a production relay replaces relay.py to isolate real Moshi latency from relay overhead.
Known non-blocking follow-ups for M2 (not required for M1): relay.py's segfault-on-teardown and crash-on-real-speech bugs need real fixes, not workarounds; relay.py has no ERROR-tag translation on upstream connection failure (from Task 5's review).

Task 7: complete (commit a5acd00, review clean/Approved). scripts/make.ps1 and Makefile both got moshi-serve (Candle server only, deliberately not the relay — avoids port conflict with moshi-bench's self-managed relay lifecycle) and moshi-bench targets, verified with real runs (moshi-serve: clean restart confirmed ~23s to ready; moshi-bench: third independent measurement p50=1606.3ms/p95=1760.4ms, consistent with prior runs). docs/M1_BRINGUP_LOG.md reorganized into one coherent document, verified by the reviewer to have lost zero facts from Tasks 1-6 (one stale 8998-port reference was corrected to 8999 in the process).

ALL 7 TASKS OF THE M1 PLAN ARE COMPLETE AND REVIEWED CLEAN.
Final whole-branch review is the next step per subagent-driven-development.
Base commit for whole-branch review: 37abe6dc6e11181a367445a36fb792a9f6dc2301 (repo state before this plan started)
Head: a5acd00
