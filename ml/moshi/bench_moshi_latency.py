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
