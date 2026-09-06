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
| 3 | Knowledge base silently reported 0 chunks; every answer ungrounded | **High** | environment / Chroma |
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

**Open robustness gap.** The application caught that exception, logged nothing,
and reported an empty corpus. An unreadable index is indistinguishable from an
empty one, so the failure mode is *silently ungrounded answers* — the worst
possible outcome for a system whose main safety property is refusing to answer
outside its corpus. **This is not yet fixed** and is the highest-value item
remaining.

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

**Current status.** Installed everything except this package. Track M hit the
identical wall independently and reached the same conclusion (see `M8` in
`PROJECT_PLAN.md`). Two people losing time to the same non-dependency is a
strong argument for demoting it to an optional extra with a comment — **not yet
done**, and worth doing before anyone else clones this.

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

**1 — The base model is running, not the fine-tuned one.** The System panel reads
`Checkpoint: base`. Track M's M6→M7→M8 chain (400 coaching pairs → QLoRA →
merged Q4_K_M GGUF) exists precisely to fix response tone. That artifact is
gitignored and lives on Track M's machine. This is the largest single factor and
the fix already exists.

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

**3 — About 12% of the corpus is not coachable prose.** Across all 1057 chunks:
110 question drills and 14 exercise lists (**124, or 11.7%**). One citation
returned for the pause query was literally back-of-chapter homework
(*"2. What are the four special effects of pause?"*). Filtering these at ingest
would help, but it shrinks the corpus, and the project's own configuration notes
are emphatic that corpus size moves the groundedness threshold — so it requires
re-running `calibrate_gate.py` afterward or refusals will drift.

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
cd backend  && pytest              # 80 passed
cd frontend && npm test            # 42 passed
cd frontend && npx tsc --noEmit    # clean
cd frontend && npm run build       # clean
```

Runtime, with all three services up:

```json
{ "llm_reachable": true, "stt_loaded": true, "corpus_chunks": 1057,
  "analyzer": "heuristic", "prompt_version": "a12-v5", "llm_variant": "base" }
```

`analyzer: heuristic` and `llm_variant: base` are both expected here — the
trained wav2vec2 checkpoint and the fine-tuned GGUF are gitignored artifacts on
Track M's machine. The M5 schema freeze means both drop in without a frontend
change.

---

## Open items

| Item | Why it matters |
|---|---|
| Unreadable Chroma index reports "empty" instead of erroring | Silently ungrounded answers; defeats the refusal guarantee |
| Make `llama-cpp-python` an optional extra | Blocks a clean install; has now cost both tracks time |
| Obtain the fine-tuned GGUF and wav2vec2 checkpoint | Largest available improvement to reply quality and analyzer fidelity |
| Filter drill/index chunks, then recalibrate the gate | Removes 11.7% non-prose from retrieval |
