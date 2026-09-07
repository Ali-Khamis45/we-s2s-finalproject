# Fix log — bringing the app up on a second machine

*Recorded 2026-09-07. Covers commits `412c216` and `d08ee68`, plus the
environment work needed to run Track A on the RTX 3060 development box.*

Six defects were found by doing one thing: installing the project from its own
`requirements.txt` on a machine that had never run it, and then actually using
it. Four were real bugs in the repository. Two were environment drift. Every
one of them is recorded below with the evidence that proved it, because a fix
without a measurement is a guess that happened to work.

---

## Summary

| # | Defect | Severity | Where |
|---|---|---|---|
| 1 | The coach had no voice — TTS silently unavailable | **High** | `backend/requirements.txt` |
| 2 | Mic button painted a 300×150 blob over the composer | **High** | `frontend/src/styles.css` |
| 3 | Knowledge base silently reported 0 chunks; every answer ungrounded | **High** | environment / Chroma, `retrieval.py` |
| 4 | Virtualenv had drifted off its pins; contract test failed | Medium | environment |
| 5 | `llama-cpp-python` cannot build, and aborts the whole install | Medium | `backend/requirements.txt` |
| 6 | TypeScript build cache was tracked in git | Low | `.gitignore` |

---

## 1. The coach had no voice

**Symptom.** Speech-to-speech worked end to end — the mic opened, the utterance
transcribed, the analyzer ran, the reply appeared as text — but nothing was ever
audible.

**Root cause.** `requirements.txt` pins `kokoro==0.3.4`. Kokoro declares its own
dependency as `misaki[en]>=0.6.5` — a **lower bound with no ceiling**. misaki 0.7
renamed `misaki.en.MutableToken` to `MToken`, so a fresh install resolves misaki
to 0.9.4 and Kokoro dies on import:

```
File ".../kokoro/pipeline.py", line 164, in KPipeline
    tokens: List[Union[en.MutableToken, List[en.MutableToken]]]
AttributeError: module 'misaki.en' has no attribute 'MutableToken'
```

This was quiet for a reason worth noting: the TTS service raises
`DependencyMissingError` and the app **degrades rather than crashing**. Every
other feature keeps working. The coach is simply mute, and nothing in the UI
says so.

**Fix.** Pinned `misaki==0.6.7` — the last release carrying the old name — with
the reason recorded beside the pin so it does not get "tidied up" later.

**Verification.** Not tested in isolation, but over a real `/ws/knowledge`
session, speaking the ground-truth dysfluent utterance:

```
audio_meta: {"sample_rate":24000, "channels":1,
             "format":"pcm_s16le", "speech_rate":0.75}
AUDIO: 583200 bytes  ≈ 12.15 s of speech
```

`speech_rate: 0.75` is the load-bearing detail. That is the acoustic branch
detecting the block and **slowing the coach's delivery down** — the behaviour
the whole project exists to demonstrate, and it had been inaudible.

---

## 2. The mic button painted a blob over the composer

**Symptom.** Pressing the mic drew a large amber shape across the composer,
covering the text input.

**Root cause.** `.orb-fluid` — the WebGL shader core inside the voice orb — was
positioned with `inset: 18%` and **no width or height**.

A `<canvas>` is a **replaced element**. When an absolutely positioned replaced
element has `width: auto`, CSS resolves its width from the element's *intrinsic*
size — `300×150` for a canvas whose width/height attributes were never set — and
then treats the four insets as over-constrained and discards one. **Insets alone
cannot size a canvas.** The rule looked correct and did nothing.

**Fix.** Declared the size the insets always implied:

```css
.orb-fluid {
  position: absolute;
  top: 18%;
  left: 18%;
  width: 64%;   /* 100% - 2 * 18% */
  height: 64%;
}
```

**Verification.** Measured from the live DOM over the Chrome DevTools Protocol,
before and after:

| | rect | drawing buffer |
|---|---|---|
| before | 300 × 150 | 300 × 150 |
| after | **59 × 59** | **59 × 59** |

59 px is exactly 64% of the 92 px orb, and the buffer followed the CSS size.

**Why it shipped.** This is the more useful finding. **No component test could
have caught it** — jsdom performs no layout, so all 41 frontend tests passed
while the page was visibly broken. And the orb only mounts its shader in the
`live` or `speaking` state, so every screenshot taken from a typed session
rendered the correct thing.

A stylesheet guard was added (`frontend/src/styles.test.ts`) asserting that an
absolutely positioned canvas declares width and height. It fails against the old
CSS and passes against the new. This required adding `@types/node`, because
vitest stubs CSS imports to an empty string and Vite's `?raw` yields nothing
under test.

---

## 3. The knowledge base silently reported zero chunks

**Symptom.** Every answer came back ungrounded with no citations. `GET
/api/status` reported `corpus_chunks: 0`. The Chroma store on disk was intact —
1057 embeddings in a `coaching_corpus` collection.

**Root cause.** The index had been written by a newer Chroma than the pinned
`chromadb==0.5.23`, which cannot read its collection configuration:

```
File ".../chromadb/api/configuration.py", line 209, in from_json
KeyError: '_type'
```

**Fix.** Rebuilt the index through the app's own ingest endpoint under the pinned
version. It came back to exactly **1057 chunks**, matching the original count.

**Verification.**

| | result |
|---|---|
| In-corpus question | grounded, 4 citations, scores 0.788 / 0.732 / 0.721 |
| Out-of-corpus question | refused, 0 citations |

Both match the documented in-corpus band of 0.696–0.814.

**Robustness gap — now fixed (2026-09-07).** The application caught that
exception, logged nothing, and reported an empty corpus. An unreadable index
was indistinguishable from an empty one, so the failure mode was *silently
ungrounded answers* — the worst possible outcome for a system whose main
safety property is refusing to answer outside its corpus.

The cause was one clause in `RetrievalService.count()`:

```python
except Exception:
    return 0        # a corrupt index and an empty one, reported identically
```

`count()` now separates the three cases. A missing `chromadb` is still `0` —
there is genuinely no corpus. Anything else raised while opening or counting
the index is logged and re-raised as a new `CorpusUnreadableError` (503,
`corpus_unreadable`) carrying the driver's own exception as `detail.cause`.

`/api/status` catches that one error rather than propagating it — the status
panel is how an operator *finds out* the corpus is broken, so it has to keep
answering — and reports a new `corpus_status` field, `"ok"` or `"unreadable"`.
The System panel now reads *"unreadable — rebuild the index"* where it used to
read *"empty"*.

**Verification.** Against a real persistent Chroma store whose collection
configuration was corrupted to reproduce the original `KeyError`:

| index | before | after |
|---|---|---|
| healthy (2 chunks) | `2` | `2` |
| corrupt | `0`, silent | raises `CorpusUnreadableError`, cause `KeyError: '_type'` |
| `/api/status` | `corpus_chunks: 0` | `corpus_chunks: 0`, `corpus_status: "unreadable"` |

Five tests in `backend/tests/test_retrieval.py` pin the distinction, including
that `/api/status` keeps answering when the corpus cannot be read. Each was
watched failing first — the status pair initially failed with a propagated 503,
which is the regression that would otherwise have taken the whole panel down.

---

## 4. The virtualenv had drifted off its pins

**Symptom.** After pulling Track M's work, `test_contract.py` failed: the
committed `docs/openapi.json` did not match the schema the app generated.

**Root cause.** Not a contract change. The installed packages had drifted far
from what the project declares:

| Package | Pinned | Installed |
|---|---|---|
| fastapi | 0.115.6 | 0.141.1 |
| pydantic | 2.10.4 | 2.13.5 |

The schema differences were entirely Pydantic serialisation changes —
`format: binary` becoming `contentMediaType`, `additionalProperties: true`
appearing on bare-dict responses. No path and no field changed.

**Fix.** Reinstalled from `requirements.txt`.

**Verification.** **80/80 backend tests pass**, contract test included.

This also confirmed a fix from Track M was load-bearing rather than cosmetic.
Youssef's commit `4c78a76` adds explicit `response_model=None` to five
`status_code=204` routes, because `from __future__ import annotations` makes a
bare `-> None` resolve to `NoneType`, which FastAPI 0.115.6 reads as a
body-bearing response model and crashes on at import. It had never surfaced
locally because the drifted FastAPI 0.141 had already fixed that bug upstream.
On the pinned version, the app only imports *because* of his change.

---

## 5. `llama-cpp-python` aborts the entire install

**Symptom.** `pip install -r requirements.txt` fails outright:

```
*** CMake build failed
ERROR: Failed building wheel for llama-cpp-python
```

No prebuilt wheel exists for this Python/MSVC combination, and because pip fails
the whole transaction, **nothing else installs either**.

**Root cause and finding.** `llama_cpp` is **never imported by any application
code**. The backend talks to an OpenAI-compatible endpoint over HTTP via `httpx`;
the package is only one suggested way to *serve* a model, mentioned in a scripts
README.

**Fixed (2026-09-07).** Track M hit the identical wall independently and
reached the same conclusion (see `M8` in `PROJECT_PLAN.md`). Re-verified before
removing it: across the whole repository the only non-documentation reference
to `llama_cpp` is a suggested serving command in `backend/scripts/README.md`.
No application code imports it.

It is now out of `requirements.txt`, replaced by a comment recording why it
must not be added back, with the `pip install` line moved next to the command
that actually needs it in the scripts README. `pip install -r requirements.txt`
now completes on a machine with no C++ toolchain.

---

## 6. TypeScript build cache was tracked

`frontend/tsconfig.tsbuildinfo` is a local incremental-build artifact that
changes on every typecheck. Removed from tracking and added to `.gitignore`
(`d08ee68`).

---

## Configuration for this machine

Not defects — recorded so the setup is reproducible. Track A's development box is
an **RTX 3060 Laptop (6 GB), Ryzen 7 5800H, 20 GB RAM**, not the RTX 5050 (8 GB)
the project plan is written around; the 5050 is Track M's machine.

**The trained artifacts are selected by `backend/.env`, not by code.** Both are
gitignored, so a machine that lacks them degrades silently to the heuristic
analyzer and the base model — which is what Track A's box reports. Where they
are present:

```bash
SCC_DYSFLUENCY_MODEL_PATH=../ml/dysfluency/checkpoints/wav2vec2-dysfluency
SCC_LLM_MODEL=qwen2.5-3b-coaching
SCC_LLM_VARIANT=finetuned
```

`/api/status` is the check: `analyzer` reads `wav2vec2-sep28k` rather than
`heuristic`, and `llm_variant` reads `finetuned`. Both are reported precisely so
a demo can never mistake scaffold output for model output.

**Moshi is disabled here.** Quantized Moshi needs ~5.5–6.0 GB and this card has
6144 MiB total with a desktop already composited on it. `SCC_MOSHI_ENABLED=false`
sends the app straight to Knowledge Mode instead of paying a connect timeout on
every probe.

**The LLM is served by Ollama**, which exposes the same OpenAI-compatible `/v1`
surface as `llama-server`, so no backend change was needed. It offloads the
1.9 GB Q4_K_M onto the GPU automatically. The effect is large:

| | CPU (llama.cpp) | GPU (Ollama) |
|---|---|---|
| Coach turn | ~28 s | **655 ms** |
| Text turn, end to end | 28.1 s | **1.2 s** |
| Spoken turn (Whisper + analyzer + RAG + LLM) | — | **4.0 s** |

The acoustic branch was re-verified on this hardware against known ground truth
— a 1400 ms block spliced in at 1345 ms was recovered as **1520 ms at
1180–2700 ms**, with both word repetitions detected, while Whisper's transcript
(`"I, I, I, want Water"`) contained no trace of the block at all.

---

## Diagnosed but not fixed: reply quality

Replies read as generic and occasionally strained. Investigated; **four
contributing causes**, ranked by impact. None is fixed yet.

**1 — The base model was running, not the fine-tuned one.** The System panel
read `Checkpoint: base`. Track M's M6→M7→M8 chain (400 coaching pairs → QLoRA →
merged Q4_K_M GGUF) exists precisely to fix response tone.

**Machine-specific, not a repo defect (checked 2026-09-07).** This was true of
Track A's box, which has neither the artifacts nor a `backend/.env`. On Track
M's machine both artifacts are present and already configured:

| artifact | path | size |
|---|---|---|
| fine-tuned GGUF | `ml/finetuning/gguf/qwen2.5-3b-coaching-Q4_K_M.gguf` | 1.9 GB |
| wav2vec2 checkpoint | `ml/dysfluency/checkpoints/wav2vec2-dysfluency/` | 378 MB |

Both are gitignored, which is why they read as missing. `backend/.env` already
sets `SCC_DYSFLUENCY_MODEL_PATH` and `SCC_LLM_VARIANT=finetuned`, and the
analyzer selects the trained classifier on load:

```
backend selected: wav2vec2-sep28k
labels          : {0: block, 1: prolongation, 2: sound_repetition,
                   3: word_repetition, 4: interjection}
```

The labels match the M5 schema contract. Nothing to fix in the repository —
this is a per-machine setup step, recorded under *Configuration* below.

**2 — The corpus is Edwardian.** Every source is public domain, so everything
predates 1930: *The Art of Public Speaking* (1915), *Vocal Expression* (1919),
*How to Become a Public Speaker* (1900). A retrieved passage for "how do I use
pauses" reads:

> *"It is often dangerous to rush into battle without pausing for preparation…
> Consider Custer's massacre as an instance."*

A 3B model paraphrasing that produces strained similes — one reply was *"pauses
are like little pauses in a song."* **Measured directly: the same model on the
same question gave a cleaner answer with retrieval OFF than ON.** Retrieval is
currently degrading reply quality rather than improving it.

**3 — Part of the corpus is not coachable prose. Fixed 2026-09-07 by demoting,
not deleting.** One citation returned for the pause query was literally
back-of-chapter homework (*"2. What are the four special effects of pause?"*).

Re-measured with a structural detector (a run of ≥3 ascending numbered items
that dominates the chunk): **56 chunks, 5.3%** — lower than the 11.7% first
estimated, because the earlier count treated any numbered line as a drill.

The obvious fix — drop them at ingest — is the wrong one, and measuring said so:

* **25% of what a structural detector flags is majority prose.** The splitter
  overlaps chunks, so genuine passages carry a numbered tail from the drill that
  follows. Deleting on a flag discards real coaching material at a 1-in-4 rate.
* **Dropping chunks moves the gate.** `retrieval_min_score` is calibrated
  against corpus size and the in/out band is only 0.077 wide.

So drills are **marked, never removed**. `ingestion.is_drill_chunk` writes
`is_drill` into chunk metadata, and `mmr_select` subtracts
`retrieval_drill_penalty` (0.15) from a flagged chunk's relevance when ranking
citations. The penalty applies *after* the groundedness gate, so the gate still
sees true similarity. A drill can still be cited when nothing better matches —
a worse citation than prose, a better one than silence.

**Verification.** Re-ingested (1057 chunks, 56 marked) and queried before/after:

| query | drills cited before | after | `best_score` |
|---|---|---|---|
| "use pauses more effectively" | 1 | **0** | 0.775 → 0.775 |
| "stop sounding monotonous" | 1 | **0** | 0.722 → 0.722 |
| "what to do with my hands" | 1 | **0** | 0.722 → 0.722 |

`best_score` is unchanged throughout — the demotion reorders citations without
touching groundedness. For the monotony query the top citation flips from
*"9. What effect do habits of thought have on confidence?"* to actual prose on
testing your delivery on a friend.

**The gate is provably unmoved**, re-run over `calibrate_gate.py`'s own
question sets:

```
in  corpus : min 0.696  median 0.720  max 0.814   grounded 10/10
out corpus : min 0.432  median 0.532  max 0.619   grounded  0/8
gap        : 0.077
```

Identical to the calibration recorded in `config.py`, so `retrieval_min_score`
needed no re-derivation — which was the entire point of demoting rather than
filtering.

**4 — 3B is small.** An A/B on the identical system prompt with retrieval off
showed an 8B staying tighter and avoiding the 3B's advice to "pause for about
three seconds", which is far too long to actually recommend.

**Ruled out.** Replies are *not* truncated — full replies run 211–285 characters
and end cleanly. Excerpt trimming is *not* the cause — `trim_excerpt` cuts on
sentence boundaries with a word-boundary fallback. And short replies are
deliberate: the system prompt specifies "two to four sentences" and
`llm_max_tokens` is 200, both to keep time-to-first-audio down.

---

## Verification

Current state of the tree, all run on this machine:

```bash
cd backend  && pytest              # 92 passed
cd frontend && npm test            # 42 passed
cd frontend && npx tsc --noEmit    # clean
cd frontend && npm run build       # clean
```

Runtime, with all three services up:

```json
{ "llm_reachable": true, "stt_loaded": true, "corpus_chunks": 1057,
  "corpus_status": "ok", "analyzer": "heuristic",
  "prompt_version": "a12-v5", "llm_variant": "base" }
```

`analyzer: heuristic` and `llm_variant: base` are what Track A's box reports —
the trained wav2vec2 checkpoint and the fine-tuned GGUF are gitignored, so a
machine without them degrades to the heuristic and the base model. On a machine
that has both (Track M's), the same build reports `wav2vec2-sep28k` and
`finetuned` with no code change: the M5 schema freeze means both drop in
without touching the frontend. See cause 1 above for the paths and the env
variables that select them.

---

## Open items

**All four closed 2026-09-07.**

| Item | Resolution |
|---|---|
| Unreadable Chroma index reported "empty" | `CorpusUnreadableError` + `corpus_status`; see §3 |
| Make `llama-cpp-python` an optional extra | Removed from `requirements.txt`; see §5 |
| Obtain the fine-tuned GGUF and wav2vec2 checkpoint | Already present and wired on Track M's box; per-machine setup, not a repo defect. See *reply quality*, cause 1 |
| Filter drill chunks, then recalibrate the gate | Superseded: chunks are **demoted, not filtered**, so no recalibration is needed. See *reply quality*, cause 3 |

Two of the four turned out not to need the fix as written. The artifacts were
never missing, only unconfigured on one machine; and filtering drill chunks
would have deleted real prose at a 1-in-4 rate and moved a gate whose in/out
band is 0.077 wide, so they are demoted at rank time instead.

**Still open — reply quality, from the section above.** Neither is a defect:

| Item | Why it matters |
|---|---|
| The corpus is Edwardian (cause 2) | Retrieval measurably *degrades* replies vs. retrieval off; needs modern source material, not a code change |
| 3B is small (cause 4) | An 8B stays tighter on the same prompt; a hardware/model-size trade, deliberately taken |
