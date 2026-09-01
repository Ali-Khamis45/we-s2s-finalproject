# Verification scripts

Evidence-producing checks that run the real production code paths against known
inputs. The pytest suite proves the plumbing without models; these prove the
behaviour *with* them, and their output is citable in the report.

Run both from `backend/` with the venv active and `PYTHONPATH=.`.

---

## `verify_acoustic_branch.py`

Proves the project's central claim: that the acoustic branch recovers what the
transcript destroys.

It builds a deliberately dysfluent utterance from real synthesized speech with
**known ground truth** — `"I ... I ... I want [1400 ms block] water please"` —
then runs it through `orchestrator.analyze_only()` and asserts the analyzer
measured what was actually spliced in.

**First generate the word audio** (Windows, no install required):

```powershell
$out = "scripts\words"
New-Item -ItemType Directory -Force -Path $out | Out-Null
Add-Type -AssemblyName System.Speech
foreach ($w in @{ "i"="I"; "want"="want"; "water"="water please" }.GetEnumerator()) {
  $s = New-Object System.Speech.Synthesis.SpeechSynthesizer
  $s.SetOutputToWaveFile("$out\$($w.Key).wav"); $s.Speak($w.Value); $s.Dispose()
}
```

On Linux or macOS, substitute any TTS (`espeak-ng -w i.wav "I"`, or `say`) — the
script only needs three mono 16-bit WAV files named `i`, `want`, `water`.

```bash
python scripts/verify_acoustic_branch.py scripts/words
```

Measured on first run (Whisper `small` int8, heuristic analyzer):

| Ground truth | Measured | Error |
|---|---|---|
| Block 1400 ms | 1480 ms | 80 ms |
| Block at 1345 ms | 1240 ms | 105 ms |
| Word repetition | detected ×3 | — |

Whisper's transcript was `"I, I, I, I, want. Water please."` — the block is
entirely absent from it. That contrast is the figure to put in the report.

Note the error bound is set by Whisper's word-timestamp resolution, not by the
analyzer. Re-run once Track M's trained classifier (M4) is wired in; it should
tighten, and `analyzer backend` in the output records which one produced the
numbers.

---

## `verify_retrieval.py`

Exercises ingestion, MMR reranking, and — the part that matters — the
groundedness gate. Uses a throwaway fixture corpus in a temp directory, never
`data/corpus`.

```bash
python scripts/verify_retrieval.py
```

**This script is how `retrieval_min_score` was calibrated, and it must be re-run
after the real corpus is ingested (A10) or the embedding model changes.**

bge embeddings have a high similarity floor — unrelated text does not score near
zero:

| Query | Score | In corpus? |
|---|---|---|
| "how do I stop speaking too fast" | 0.71 | yes |
| "how do I change a diesel oil filter" | 0.48 | **no** |
| "what is the capital of Mongolia" | 0.35 | **no** |

The original default of 0.28 admitted all three, which meant the coach would
have answered an out-of-corpus question from weak matches instead of saying it
had no material. The gate now sits at 0.55. Being unhelpful about technique is
much better than being confidently wrong about it.
