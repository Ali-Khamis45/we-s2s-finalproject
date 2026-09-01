"""Which Whisper model should the cascade use?

Speed alone is the wrong question. The acoustic branch derives block duration
and pause length from Whisper's WORD TIMESTAMPS, so a faster model that reports
sloppier timings buys latency at the cost of the project's central measurement.

This measures both on the same ground-truth audio: how fast each model
transcribes, and whether the 1400 ms block spliced into it still comes back at
the right length and in the right place.
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

TMP = Path(tempfile.mkdtemp(prefix="scc-wb-"))
os.environ["SCC_DATA_DIR"] = str(TMP)
os.environ["SCC_DATABASE_URL"] = f"sqlite+aiosqlite:///{(TMP / 'w.db').as_posix()}"
os.environ["SCC_MOSHI_ENABLED"] = "false"

WAV = Path(sys.argv[1])
MODELS = sys.argv[2].split(",") if len(sys.argv) > 2 else ["tiny", "base", "small"]
REPEATS = 3

BLOCK_TRUTH_MS = 1400
BLOCK_START_TRUTH_MS = 1345


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as wf:
        rate = wf.getframerate()
        frames = wf.readframes(wf.getnframes())
    return np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0, rate


async def main() -> int:
    from faster_whisper import WhisperModel

    from app.services.dysfluency import HeuristicBackend
    from app.services.stt import Transcript, Word

    audio, rate = read_wav(WAV)
    audio_s = audio.size / rate
    analyzer = HeuristicBackend()

    print("=" * 78)
    print(f"WHISPER MODEL COMPARISON   ({audio_s:.2f}s utterance, CPU int8, {REPEATS} runs each)")
    print(f"ground truth: a {BLOCK_TRUTH_MS} ms block starting at {BLOCK_START_TRUTH_MS} ms")
    print("=" * 78)

    rows = []

    for name in MODELS:
        print(f"\n  loading {name}…", flush=True)
        try:
            model = await asyncio.to_thread(
                WhisperModel, name, device="cpu", compute_type="int8"
            )
        except Exception as exc:
            print(f"    could not load: {exc}")
            continue

        def run() -> Transcript:
            segments, _ = model.transcribe(
                audio,
                language="en",
                word_timestamps=True,
                vad_filter=True,
                beam_size=1,
                condition_on_previous_text=False,
            )
            parts, words = [], []
            for seg in segments:
                parts.append(seg.text)
                for w in getattr(seg, "words", None) or []:
                    words.append(
                        Word(
                            text=w.word.strip(),
                            start_ms=int(w.start * 1000),
                            end_ms=int(w.end * 1000),
                        )
                    )
            return Transcript(
                text=" ".join(p.strip() for p in parts).strip(),
                words=words,
                duration_ms=int(audio_s * 1000),
            )

        # warm, then measure
        transcript = await asyncio.to_thread(run)
        times = []
        for _ in range(REPEATS):
            t = time.perf_counter()
            transcript = await asyncio.to_thread(run)
            times.append((time.perf_counter() - t) * 1000)

        profile = await analyzer.analyze(audio, rate, transcript)
        blocks = [e for e in profile.events if e.kind.value == "block"]
        longest = max(blocks, key=lambda e: e.duration_ms) if blocks else None

        p50 = statistics.median(times)
        rows.append(
            {
                "model": name,
                "ms": p50,
                "rtf": p50 / (audio_s * 1000),
                "words": len(transcript.words),
                "block_ms": longest.duration_ms if longest else None,
                "block_err": abs(longest.duration_ms - BLOCK_TRUTH_MS) if longest else None,
                "start_err": abs(longest.start_ms - BLOCK_START_TRUTH_MS) if longest else None,
                "events": dict(profile.event_counts),
                "text": transcript.text,
            }
        )
        print(f"    {p50:.0f} ms   {transcript.text!r}")

    print("\n" + "=" * 78)
    print(f"  {'model':<8} {'STT p50':>9} {'RTF':>6} {'words':>6} "
          f"{'block':>9} {'dur err':>8} {'pos err':>8}")
    print("  " + "-" * 74)
    for r in rows:
        block = f"{r['block_ms']} ms" if r["block_ms"] else "MISSED"
        derr = f"{r['block_err']} ms" if r["block_err"] is not None else "—"
        perr = f"{r['start_err']} ms" if r["start_err"] is not None else "—"
        print(f"  {r['model']:<8} {r['ms']:>7.0f}ms {r['rtf']:>6.2f} {r['words']:>6} "
              f"{block:>9} {derr:>8} {perr:>8}")

    print("\n  events detected per model:")
    for r in rows:
        print(f"    {r['model']:<8} {r['events']}")

    # Recommendation, derived rather than asserted.
    usable = [r for r in rows if r["block_err"] is not None and r["block_err"] <= 350]
    print("\n" + "=" * 78)
    print("RECOMMENDATION")
    print("=" * 78)
    if not usable:
        print("  No model kept the block within 350 ms. Do not shrink Whisper further.")
    else:
        best = min(usable, key=lambda r: r["ms"])
        saving = max(r["ms"] for r in rows) - best["ms"]
        print(f"""
  Fastest model that still measures the block accurately: {best['model']}
    STT {best['ms']:.0f} ms  (RTF {best['rtf']:.2f})
    block measured {best['block_ms']} ms vs {BLOCK_TRUTH_MS} ms truth, off by {best['block_err']} ms
    saves {saving:.0f} ms per turn against the slowest model tested

  Set SCC_WHISPER_MODEL={best['model']} if latency matters more than transcript
  quality for the demo. Note the transcript is only used for the LLM prompt and
  history - the acoustic measurements come from the word timings, which is what
  the block-error column above actually validates.
""")
    return 0


if __name__ == "__main__":
    import shutil

    try:
        raise SystemExit(asyncio.run(main()))
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
