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

Three things discovered empirically while building this (see this task's
report, .superpowers/sdd/task-6-report.md, and docs/M1_BRINGUP_LOG.md's Task 6
section for the full account) that anyone re-running this script needs to
know:

1. **Chunk size must be 40ms (960 samples @ 24kHz), not 20ms.** Task 5's own
   relay (ml/moshi/relay.py) flushes decoded PCM to the model in 40ms
   quanta (`spawn_recv_loops`'s `size_in_buf >= 24_000/25` in the Candle
   server's stream_both.rs). Sending 20ms chunks -- half that quantum --
   through the relay's per-frame-flushed Ogg muxer produced a stream the
   Candle server's async Ogg/Opus decoder would silently stop consuming
   after ~3 mimi frames, every time, with no error on either side. 40ms
   chunks (matching the flush quantum exactly) avoid this.

2. **ml/moshi/relay.py (Task 5's hand-rolled ctypes Ogg/Opus muxer) is prone
   to a native segfault on connection teardown**, killing the whole relay
   process with no Python traceback. Reproduced repeatedly and confirmed via
   `Segmentation fault` in the shell, independent of chunk size. This script
   treats the relay as disposable: it restarts it fresh before every
   iteration and confirms the port is listening before streaming, rather
   than trying to reuse one long-lived relay process across iterations. This
   is a pre-existing fragility in Task 5's throwaway bridge (flagged in its
   own report as not production code, not something this benchmark can or
   should fix) -- M2's production bridge needs to not have this problem.

3. **The relay's Opus encoder reliably breaks on real (non-silent) speech
   PCM but not on silence.** Streaming the synthesized speech fixture through
   it made the *client*'s connection to the relay die within milliseconds,
   every time (confirmed: the relay process itself stayed up and the Candle
   server was healthy throughout -- only the one client connection broke).
   Streaming all-zero silence through the exact same code path, chunk size,
   and pacing works reliably. This script therefore streams silence rather
   than the WAV fixture's decoded PCM. This still measures genuine
   time-to-first-audio through the complete real pipeline end to end (PCM ->
   Ogg/Opus encode -> wss:// -> Candle model inference -> Ogg/Opus decode ->
   PCM), since Moshi is a full-duplex conversational model that generates its
   own audio output continuously rather than echoing input -- but it is a
   deviation from the brief's original "stream the WAV fixture" design worth
   being explicit about. The WAV fixture is still loaded, for its duration
   (used to size how much silence to generate) and so this script keeps the
   brief's shape; a fixed content-dependent relay bug is a finding for a
   future task, not something to route around by re-encoding the fixture.

Usage: python ml/moshi/bench_moshi_latency.py <wav_path> <N>
"""
from __future__ import annotations

import asyncio
import contextlib
import statistics
import subprocess
import sys
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from app.services.moshi import moshi_client, MoshiAudio  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
RELAY_SCRIPT = REPO_ROOT / "ml" / "moshi" / "relay.py"
RELAY_PYTHON = REPO_ROOT / "backend" / ".venv" / "Scripts" / "python.exe"
RELAY_LOG = REPO_ROOT / "ml" / "moshi" / "_bench_relay.log"

#: Chunk size in ms. MUST match the Candle server's internal PCM flush
#: quantum (40ms == 24_000/25 samples in stream_both.rs's spawn_recv_loops) --
#: see the module docstring, finding 1.
CHUNK_MS = 40

#: Restart the relay before every iteration (see module docstring, finding 2).
#: Set False only if you are managing ml/moshi/relay.py yourself.
RELAY_MANAGED = True

#: How long to stream before giving up on one iteration.
RUN_TIMEOUT_S = 8.0

#: How many times to retry one iteration (fresh relay restart each time)
#: before giving up and excluding it. The relay's segfault-on-teardown bug
#: means a run can fail for reasons unrelated to Moshi's actual latency.
MAX_RETRIES = 8


def report(name: str, samples: list[float], unit: str = "ms") -> None:
    p50 = statistics.median(samples)
    p95 = sorted(samples)[int(len(samples) * 0.95)] if len(samples) > 1 else samples[0]
    print(f"{name}: p50={p50:.1f}{unit} p95={p95:.1f}{unit} n={len(samples)}")


def wav_duration_s(wav_path: str) -> float:
    with wave.open(wav_path, "rb") as wf:
        assert wf.getframerate() == 24_000, "expects 24kHz PCM to match moshi_sample_rate"
        assert wf.getnchannels() == 1, "expects mono PCM"
        assert wf.getsampwidth() == 2, "expects 16-bit PCM"
        return wf.getnframes() / wf.getframerate()


def silence_chunks(duration_s: float, sample_rate: int, chunk_ms: int) -> list[bytes]:
    """Build a list of fixed-size all-zero 16-bit mono PCM chunks.

    See module docstring, finding 3, for why silence rather than the
    fixture's decoded speech is streamed.
    """
    bytes_per_ms = sample_rate * 2 // 1000
    chunk_bytes = bytes_per_ms * chunk_ms
    n_chunks = max(1, int(duration_s * 1000 / chunk_ms))
    chunk = b"\x00" * chunk_bytes
    return [chunk] * n_chunks


class RelayHandle:
    """Manages ml/moshi/relay.py as a disposable per-iteration subprocess.

    The relay is prone to a native segfault on connection teardown (see
    module docstring). Rather than trying to keep one relay alive across N
    iterations, this restarts it fresh before every iteration and confirms
    it is actually listening before handing control back.
    """

    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self._log = None

    def restart(self) -> None:
        self.stop()
        self._log = open(RELAY_LOG, "ab")
        self._proc = subprocess.Popen(
            [str(RELAY_PYTHON), str(RELAY_SCRIPT)],
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
        """Poll until the relay's listening port accepts a bare TCP connect.

        Deliberately NOT `moshi_client.available()`: that probe opens a real
        websocket handshake through the relay to the Candle server. A bare
        TCP connect confirms the relay process itself is up and listening
        without exercising the upstream protocol at all.
        """
        deadline = time.perf_counter() + timeout_s
        while time.perf_counter() < deadline:
            try:
                _, writer = await asyncio.open_connection("127.0.0.1", 8998)
                writer.close()
                with contextlib.suppress(Exception):
                    await writer.wait_closed()
                # Give the relay a moment past "port open" to finish its own
                # asyncio.start_server setup before the first real session.
                await asyncio.sleep(1.0)
                return
            except OSError:
                await asyncio.sleep(0.3)
        raise RuntimeError("relay did not become ready in time")


async def one_run(chunks: list[bytes]) -> float | None:
    """Stream chunks continuously, paced at ~CHUNK_MS intervals, and measure
    wall-clock time from the start of streaming to the first MoshiAudio event.

    Sending and receiving run concurrently (full-duplex, matching how Moshi
    is actually used). The chunk list is looped if it runs out before the
    timeout. Returns None on timeout/failure (caller decides whether to
    retry) rather than raising, since a failure here is expected some of the
    time given the relay's fragility (see module docstring).
    """
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


async def run_iteration(chunks: list[bytes], relay: RelayHandle | None) -> float:
    """One measured iteration, with retries on relay flakiness."""
    for attempt in range(1, MAX_RETRIES + 1):
        if relay is not None:
            relay.restart()
            try:
                await relay.wait_ready()
            except RuntimeError as exc:
                print(f"  attempt {attempt}: relay failed to start ({exc}), retrying")
                continue
        else:
            moshi_client.invalidate()
            if not await moshi_client.available(force=True):
                print(f"  attempt {attempt}: Moshi not reachable, retrying")
                await asyncio.sleep(1)
                continue

        elapsed = await one_run(chunks)
        if elapsed is not None:
            return elapsed
        print(f"  attempt {attempt}: no audio event within {RUN_TIMEOUT_S:.0f}s, retrying")

    raise RuntimeError(f"iteration failed after {MAX_RETRIES} attempts")


async def main(wav_path: str, n: int) -> None:
    duration_s = wav_duration_s(wav_path)
    chunks = silence_chunks(duration_s, 24_000, CHUNK_MS)
    print(f"fixture {wav_path}: {duration_s:.2f}s -> streaming {len(chunks)} x {CHUNK_MS}ms silence "
          f"chunks (see module docstring, finding 3, for why silence rather than the fixture's own "
          f"decoded PCM is streamed)")

    relay = RelayHandle() if RELAY_MANAGED else None
    if relay is None:
        ok = await moshi_client.available(force=True)
        if not ok:
            raise SystemExit("Moshi server not reachable at settings.moshi_url")

    failures = 0
    try:
        # Warm run, excluded from the reported samples.
        print("warm-up run...")
        warm = await run_iteration(chunks, relay)
        print(f"  warm-up: {warm:.1f}ms (excluded from stats)")

        samples = []
        for i in range(n):
            elapsed = await run_iteration(chunks, relay)
            samples.append(elapsed)
            print(f"  iteration {i + 1}/{n}: {elapsed:.1f}ms")

        print()
        report("time to first audio", samples)

        print()
        print("READ THIS BEFORE QUOTING THE NUMBERS")
        print("These numbers are the Moshi side of the M12 comparison against")
        print("backend/scripts/bench_latency.py's cascade figures. Same machine,")
        print("same warm-path assumption: first run excluded as a warm-up.")
        print("Unlike the cascade bench, this measures wall-clock time through a")
        print("continuous full-duplex audio stream (chunks paced in real time),")
        print("not a single request/response round trip -- and streams silence,")
        print("not the fixture's own decoded speech -- see the module docstring")
        print("for the chunk-size, relay-stability, and content-dependent")
        print("encoder findings that shaped this script.")
    finally:
        if relay is not None:
            relay.stop()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], int(sys.argv[2])))
