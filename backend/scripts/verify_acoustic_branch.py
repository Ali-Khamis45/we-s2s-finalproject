"""End-to-end verification of the acoustic branch.

Builds a deliberately dysfluent utterance from real synthesized speech with
KNOWN ground truth, then runs it through the actual production path
(orchestrator.analyze_only -> Whisper -> DysfluencyAnalyzer) and checks that
what comes out matches what went in.

The utterance:   "I ... I ... I want [1400 ms block] water please"

This is the project's central claim under test. A conventional pipeline would
report "I want water please" and nothing else. The acoustic branch should
recover the repetition and the silent block, and measure the block's duration
close to the 1400 ms actually inserted.
"""

from __future__ import annotations

import asyncio
import sys
import wave
from pathlib import Path

import numpy as np

WORDS = Path(sys.argv[1])
RATE = 16_000
BLOCK_MS = 1_400          # ground truth: the block we splice in
REPEAT_GAP_MS = 90        # gap between the repeated "I"s
PHRASE_GAP_MS = 140


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as wf:
        channels, width, rate = wf.getnchannels(), wf.getsampwidth(), wf.getframerate()
        frames = wf.readframes(wf.getnframes())
    assert width == 2, f"expected 16-bit, got {width * 8}-bit"
    samples = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    return samples, rate


def resample(x: np.ndarray, src: int, dst: int) -> np.ndarray:
    if src == dst:
        return x
    n = int(round(x.size * dst / src))
    return np.interp(
        np.linspace(0, x.size - 1, n), np.arange(x.size), x
    ).astype(np.float32)


def trim(x: np.ndarray, thresh: float = 0.015) -> np.ndarray:
    """Strip the silence SAPI pads around each utterance."""
    loud = np.where(np.abs(x) > thresh)[0]
    if loud.size == 0:
        return x
    pad = int(0.02 * RATE)
    return x[max(0, loud[0] - pad) : min(x.size, loud[-1] + pad)]


def silence(ms: int) -> np.ndarray:
    # Not digital zero: a real room has a noise floor, and the endpointer's
    # adaptive threshold should see something plausible.
    n = int(RATE * ms / 1000)
    return (np.random.randn(n) * 0.0004).astype(np.float32)


def build() -> tuple[np.ndarray, dict]:
    parts: dict[str, np.ndarray] = {}
    for name in ("i", "want", "water"):
        raw, rate = read_wav(WORDS / f"{name}.wav")
        parts[name] = trim(resample(raw, rate, RATE))

    segments: list[np.ndarray] = []
    marks: dict[str, int] = {}

    def add(chunk: np.ndarray) -> None:
        segments.append(chunk)

    def now_ms() -> int:
        return int(sum(s.size for s in segments) / RATE * 1000)

    # "I ... I ... I"  — a word repetition
    marks["repetition_start_ms"] = now_ms()
    for k in range(3):
        add(parts["i"])
        if k < 2:
            add(silence(REPEAT_GAP_MS))
    marks["repetition_end_ms"] = now_ms()

    add(silence(PHRASE_GAP_MS))
    add(parts["want"])

    # The block: a long silence mid-sentence.
    marks["block_start_ms"] = now_ms()
    add(silence(BLOCK_MS))
    marks["block_end_ms"] = now_ms()

    add(parts["water"])

    audio = np.concatenate(segments).astype(np.float32)
    marks["total_ms"] = int(audio.size / RATE * 1000)
    return audio, marks


async def main() -> int:
    audio, truth = build()

    out = WORDS.parent / "dysfluent_utterance.wav"
    with wave.open(str(out), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(RATE)
        wf.writeframes((np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes())

    print("=" * 68)
    print("GROUND TRUTH (what we spliced in)")
    print("=" * 68)
    print(f"  utterance length     {truth['total_ms']} ms  -> {out.name}")
    print(f"  word repetition      'I' x3 at {truth['repetition_start_ms']}–{truth['repetition_end_ms']} ms")
    print(f"  block                {BLOCK_MS} ms at {truth['block_start_ms']}–{truth['block_end_ms']} ms")

    from app.services.orchestrator import orchestrator

    print("\n" + "=" * 68)
    print("PRODUCTION PATH  (Whisper -> DysfluencyAnalyzer)")
    print("=" * 68)
    transcript, profile = await orchestrator.analyze_only(audio, RATE)

    print(f"\n  Whisper transcript:  {transcript.text!r}")
    print("  ^ this is ALL a conventional cascade would have kept\n")

    print(f"  analyzer backend     {profile.source}")
    print(f"  measured duration    {profile.duration_ms} ms")
    print(f"  events detected      {profile.event_counts}")
    print(f"  fluency load         {profile.fluency_load}")
    print(f"  suggested TTS rate   {profile.suggested_speech_rate(floor=0.75, ceiling=1.15)}")

    print("\n  timeline:")
    for e in profile.events:
        print(f"    {e.kind.value:18} {e.start_ms:>6}–{e.end_ms:<6} ms  "
              f"({e.duration_ms:>5} ms, conf {e.confidence:.2f})")

    print("\n  prompt block handed to the LLM:")
    for line in profile.to_prompt_block().splitlines():
        print(f"    {line}")

    # ---- assertions against ground truth ----
    print("\n" + "=" * 68)
    print("CHECKS")
    print("=" * 68)
    failures: list[str] = []

    def check(label: str, ok: bool, detail: str = "") -> None:
        print(f"  {'PASS' if ok else 'FAIL'}  {label}{('  — ' + detail) if detail else ''}")
        if not ok:
            failures.append(label)

    check("transcript is non-empty", not transcript.is_empty, repr(transcript.text))
    check("analyzer ran", profile.analyzed, profile.source)

    blocks = [e for e in profile.events if e.kind.value == "block"]
    check("a block was detected", bool(blocks), f"{len(blocks)} found")

    if blocks:
        longest = max(blocks, key=lambda e: e.duration_ms)
        err = abs(longest.duration_ms - BLOCK_MS)
        check(
            f"block duration within 350 ms of the {BLOCK_MS} ms inserted",
            err <= 350,
            f"measured {longest.duration_ms} ms, off by {err} ms",
        )
        pos_err = abs(longest.start_ms - truth["block_start_ms"])
        check(
            "block located within 400 ms of where it was inserted",
            pos_err <= 400,
            f"measured start {longest.start_ms} ms vs {truth['block_start_ms']} ms",
        )

    check(
        "duration measured correctly",
        abs(profile.duration_ms - truth["total_ms"]) <= 60,
        f"{profile.duration_ms} vs {truth['total_ms']} ms",
    )
    check(
        "acoustic context reaches the prompt",
        "<acoustic_context>" in profile.to_prompt_block(),
    )
    check(
        "coach is told to slow down",
        profile.suggested_speech_rate(floor=0.75, ceiling=1.15) < 1.0,
    )

    print()
    if failures:
        print(f"{len(failures)} check(s) failed: {', '.join(failures)}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
