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

## `fetch_corpus.py`

Builds the knowledge base (A10). `data/corpus/` is gitignored, so this script
*is* the reproducible record of what the corpus contains.

```bash
python scripts/fetch_corpus.py --dry-run   # screen only, write nothing
python scripts/fetch_corpus.py             # download, screen, write
```

Five public-domain Project Gutenberg books on public speaking and vocal
delivery, 313,866 words → 1,057 chunks. Full provenance in
[`data/corpus/SOURCES.md`](../../data/corpus/SOURCES.md).

**It screens for scope, and the screen matters.** Period elocution texts
sometimes contain chapters on "curing" stammering, written with attitudes that
are clinical and demeaning by any modern standard — exactly what
[ETHICS.md](../../docs/ETHICS.md) excludes.

The screen looks for *clustering* of pathologising terms, not raw frequency. An
earlier version counted "cure", "patient", "treatment" and "defect" too, and
rejected four of six books — because in 1915 prose those are ordinary words
("the *treatment* of the subject", "*patient* effort"). It was measuring period
English, not clinical framing.

It also prints the surrounding text for every hit, because the automation only
narrows the field. **One book was excluded on human review**: Stratton's
*Public Speaking* (1920) passed the cluster check but claims a stammerer can be
"relieved, if not cured… by attention to the position of the vocal organs". A
false cure claim about the people this tool serves. That exclusion is recorded
in SOURCES.md and belongs in the report's ethics section.

---

## `calibrate_gate.py`

Re-derives `SCC_RETRIEVAL_MIN_SCORE` against the real corpus. Run after any
change to the corpus or the embedding model.

```bash
python scripts/calibrate_gate.py --reingest
```

**Corpus size moves this threshold, which is the whole reason the script
exists.** A gate of 0.55 derived from a three-document fixture would have
answered 3 of 8 out-of-corpus questions once the real books were indexed — a
larger corpus offers more chances for a spurious high match ("how do I train for
a marathon" reaches 0.619 against a public-speaking corpus).

Measured over 1,057 chunks:

| Set | min | median | max |
|---|---|---|---|
| In corpus (10 questions) | 0.696 | 0.720 | 0.814 |
| Out of corpus (8 questions) | 0.434 | 0.532 | 0.619 |

At the resulting **0.65**: 0/8 out-of-corpus answered, 0/10 in-corpus refused.
The threshold sits in the 0.077 gap, nearer the out-of-corpus side on purpose —
the errors are not symmetric.

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

---

## `verify_cascade.py`

The whole Knowledge Mode path end to end, driven through the real FastAPI app:
retrieval, prompt assembly, generation, streaming, and persistence.

Needs a served model first:

```bash
python -m llama_cpp.server --model <path>.gguf --model_alias qwen-test \
  --port 8080 --n_ctx 4096 --chat_format chatml
python scripts/verify_cascade.py scripts/words/dysfluent_utterance.wav
```

Covers the five things no other check reaches: a grounded cited answer, the
gate refusing an out-of-corpus question, a spoken turn carrying its acoustic
context into the prompt, incremental SSE streaming, and unified history across
modes.

---

## `bench_latency.py`

Warm-path per-stage latency, p50 and p95. Feeds M10 and M12.

```bash
python scripts/bench_latency.py scripts/words/dysfluent_utterance.wav 5
```

**This replaced an estimate with a measurement, and the estimate was wrong.**
The plan originally claimed 750 ms – 1.1 s to first audio. Measured: **~1.9 s**
on a 0.5B model, and the project ships 3B. Expect seconds.

That is not bad news for the thesis — it widens the gap against Moshi's
~200 ms, which is exactly the comparison M12 exists to make.

---

## `bench_whisper.py`

Which Whisper model the cascade should use — decided on word-timestamp
accuracy, not transcript quality, because the acoustic branch measures block
duration from the timings.

```bash
python scripts/bench_whisper.py scripts/words/dysfluent_utterance.wav tiny,base,small
```

Measured against a 1400 ms block spliced into a 3.6 s utterance:

| Model | STT p50 | RTF | Block measured | Error |
|---|---|---|---|---|
| tiny | 285 ms | 0.08 | **missed entirely** | — |
| **base** | **598 ms** | **0.17** | **1440 ms** | **40 ms** |
| small | 2025 ms | 0.56 | 1460 ms | 60 ms |

`base` is the default: 3.4× faster than `small` and marginally *more* accurate
on the measurement that matters.

**`tiny` is disqualified, and worth a paragraph in the report.** It transcribed
`"I-I-I want ... water"` as `"I want water please"` — normalising away both the
repetitions and the 1.4 s pause. A smaller ASR model does not merely lose
accuracy on disfluent speech; it actively fluent-izes it. That is the project's
central claim, demonstrated as a measurement rather than asserted.
