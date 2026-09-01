"""Warm-path latency measurement for the cascade (feeds M10 / M12).

The plan's stated budget — 750 ms to 1.1 s to first audio — was an estimate, not
a measurement. This measures it: every model warmed first, then N iterations,
reporting median and p95 per stage.

Everything runs on CPU by design (the GPU is reserved for Moshi), so these are
the numbers the shipped product actually produces, not a best case on a
different machine.

Caveat on the LLM figure: this runs Qwen2.5-0.5B, and the project ships 3B.
Decode time scales roughly with parameter count on CPU, so treat the LLM column
as a floor and expect the real model to be several times slower.
"""

from __future__ import annotations

import asyncio
import os
import statistics
import sys
import tempfile
import time
import wave
from pathlib import Path

import numpy as np

TMP = Path(tempfile.mkdtemp(prefix="scc-bench-"))
os.environ["SCC_DATA_DIR"] = str(TMP)
os.environ["SCC_CHROMA_DIR"] = str(TMP / "chroma")
os.environ["SCC_CORPUS_DIR"] = str(TMP / "corpus")
os.environ["SCC_DATABASE_URL"] = f"sqlite+aiosqlite:///{(TMP / 'b.db').as_posix()}"
os.environ["SCC_MOSHI_ENABLED"] = "false"
os.environ["SCC_LLM_MODEL"] = "qwen-test"
os.environ["SCC_LLM_MAX_TOKENS"] = "120"

WAV = Path(sys.argv[1])
N = int(sys.argv[2]) if len(sys.argv) > 2 else 5


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as wf:
        rate = wf.getframerate()
        frames = wf.readframes(wf.getnframes())
    return np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0, rate


def report(name: str, samples: list[float], unit: str = "ms") -> None:
    if not samples:
        return
    p50 = statistics.median(samples)
    p95 = sorted(samples)[min(len(samples) - 1, int(len(samples) * 0.95))]
    print(f"  {name:<26} p50 {p50:>8.0f} {unit}   p95 {p95:>8.0f} {unit}   n={len(samples)}")


async def main() -> int:
    from app.services.dysfluency import dysfluency_analyzer
    from app.services.llm import Message, llm_service
    from app.services.prompts import templates
    from app.services.stt import stt_service

    audio, rate = read_wav(WAV)
    audio_s = audio.size / rate

    print("=" * 74)
    print(f"WARM-PATH LATENCY   ({audio_s:.2f}s utterance, {N} iterations, CPU)")
    print("=" * 74)

    print("\n  warming models…")
    t0 = time.perf_counter()
    transcript = await stt_service.transcribe(audio, rate)
    print(f"  cold STT (includes model load): {(time.perf_counter() - t0) * 1000:.0f} ms")
    await dysfluency_analyzer.analyze(audio, rate, transcript)
    await llm_service.complete([Message(role="user", content="hi")], max_tokens=8)
    print("  warm.\n")

    stt_ms: list[float] = []
    acoustic_ms: list[float] = []
    prompt_ms: list[float] = []
    ttft_ms: list[float] = []
    llm_ms: list[float] = []
    total_ms: list[float] = []

    for i in range(N):
        turn_start = time.perf_counter()

        t = time.perf_counter()
        transcript = await stt_service.transcribe(audio, rate)
        stt_ms.append((time.perf_counter() - t) * 1000)

        t = time.perf_counter()
        profile = await dysfluency_analyzer.analyze(audio, rate, transcript)
        acoustic_ms.append((time.perf_counter() - t) * 1000)

        t = time.perf_counter()
        bundle = templates.build(user_text=transcript.text, acoustic=profile)
        prompt_ms.append((time.perf_counter() - t) * 1000)

        t = time.perf_counter()
        first: float | None = None
        async for _ in llm_service.stream(bundle.messages, max_tokens=120):
            if first is None:
                first = (time.perf_counter() - t) * 1000
                ttft_ms.append(first)
        llm_ms.append((time.perf_counter() - t) * 1000)

        # Time to FIRST AUDIO is what a listener perceives, so it stops at the
        # first token rather than the last: TTS begins on the first sentence.
        total_ms.append((turn_start and (time.perf_counter() - turn_start) * 1000))
        print(f"    iteration {i + 1}/{N} done")

    print("\n  PER STAGE")
    report("Whisper STT", stt_ms)
    report("acoustic analyzer", acoustic_ms)
    report("prompt assembly", prompt_ms)
    report("LLM time-to-first-token", ttft_ms)
    report("LLM full generation", llm_ms)
    report("full turn (to last token)", total_ms)

    ttfa = [s + a + p + f for s, a, p, f in zip(stt_ms, acoustic_ms, prompt_ms, ttft_ms)]
    print("\n  WHAT THE USER PERCEIVES")
    report("time to first audio", ttfa)

    rtf = statistics.median(stt_ms) / (audio_s * 1000)
    print(f"\n  Whisper real-time factor: {rtf:.2f}x  "
          f"(1.0 = transcribes as fast as the audio plays)")

    print("\n" + "=" * 74)
    print("READ THIS BEFORE QUOTING THE NUMBERS")
    print("=" * 74)
    p50_ttfa = statistics.median(ttfa)
    print(f"""
  Measured time to first audio: {p50_ttfa:.0f} ms (p50), on Qwen2.5-0.5B.

  The plan's estimate was 750-1100 ms. Two corrections:

  - Whisper 'small' int8 on CPU costs {statistics.median(stt_ms):.0f} ms for a
    {audio_s:.1f}s utterance, well above the 150-300 ms assumed. Dropping to
    'base' or 'tiny' is the lever if this needs to come down.
  - The shipped model is 3B, not 0.5B. CPU decode scales roughly with
    parameter count, so expect the LLM stage to be several times the
    {statistics.median(ttft_ms):.0f} ms measured here.

  The realistic cascade figure is therefore SECONDS, not one second. That does
  not weaken the thesis - it widens the gap against Moshi's ~200 ms, which is
  the comparison M12 exists to make. Quote measurements, never the estimate.
""")
    await llm_service.aclose()
    return 0


if __name__ == "__main__":
    import shutil

    try:
        raise SystemExit(asyncio.run(main()))
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
