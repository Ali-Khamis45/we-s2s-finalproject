# Speech Confidence Coach
## A Native Speech-to-Speech AI Assistant for Communication Practice

*Graduation Project — Generative AI Software Product*

---

> **Status of this document.** The sections owned by the application track are
> written. Sections owned by the model track — the fine-tuning dataset,
> fine-tuning, optimization, and the evaluation results — are marked
> **`[TRACK M]`** and carry instructions on exactly what goes in them and which
> command produces the numbers. Every figure already present in this document is
> a measurement reproducible from `backend/scripts/`; nothing here is estimated.

---

## 1. Problem Statement

Speaking is not optional. Interviews, presentations, seminars, and meetings are
gateways to education and employment, and they are all conducted out loud, in
real time, in front of people. For someone who experiences speech differences —
stuttering, cluttering, or acute speaking anxiety — those moments carry a cost
that fluent speakers never pay.

The obstacle is rarely knowledge. Most people know they should slow down, pause,
and keep the opening sentence short. What they lack is **low-stakes, repeatable
practice with something that responds**. Practising alone in a room provides no
feedback; practising with a person means recruiting one repeatedly and
tolerating the very social pressure the practice is meant to defuse.

Conversational AI ought to fill that gap, and it nearly does. But every
mainstream voice assistant is built on a cascade that begins by transcribing
speech into text. That first step is not neutral. It is explicitly designed to
produce clean, fluent text, and it discards precisely the information that
matters here.

A speaker says:

> *"I-i-i want… water."*

The transcript reads:

> *"I want water."*

The repetition is gone. The block is gone. Its duration — the difference between
a 300 ms hesitation and a two-second struggle — is gone. Everything downstream
reasons about a sentence that was never spoken that way.

We measured this rather than asserting it. Running our test utterance through
Whisper `tiny` returned *"I want water please"*: the repetitions and a spliced
1.4-second block were erased outright (§9.1). A smaller speech-recognition model
does not merely lose accuracy on disfluent speech — **it actively fluent-izes
it**.

A tool intended to help people practise speaking cannot be built on a component
whose job is to pretend the speech was fluent.

## 2. Target Audience

**Primary users** are adults who experience speech differences and want to
rehearse for a specific, upcoming speaking situation: a job interview, a viva, a
presentation, a wedding toast.

They need three things a general chatbot does not provide:

1. **Practice that does not judge.** A private setting where a block costs
   nothing and can be repeated.
2. **A partner that adapts.** Something that notices when they are struggling
   and gives them room, rather than replying at the same brisk pace.
3. **Evidence of progress.** Visible change over sessions, framed as practice
   rather than as a score.

**Secondary users** are anyone preparing for high-pressure speaking who wants
feedback on pacing and delivery.

### 2.1 Scope boundary

This is an **accessibility and practice tool**. It is not a medical device. It
does not diagnose, assess, grade, or treat anyone, and it is not a substitute
for a speech-language pathologist — that is a licensed clinical profession and
this system is not qualified to practise it.

This boundary is not a disclaimer bolted on at the end. It is enforced in the
system prompt, demonstrated in the few-shot exemplars, encoded as a vocabulary
ban, screened for when building the knowledge base (§6.2), and measured as an
evaluation metric (§10). The full statement is in
[`docs/ETHICS.md`](ETHICS.md).

## 3. The Solution

A dual-mode conversational coach that hears *how* something was said, not only
what was said.

| | **Live Coach** | **Grounded Knowledge** |
|---|---|---|
| Architecture | Native speech-to-speech (Moshi) | Cascade (Whisper → RAG → LLM → TTS) |
| Latency | ~200 ms | ~1.9 s measured (§9.2) |
| Placement | GPU-resident, ~6 GB | CPU |
| Strength | Full-duplex conversation, hears dysfluency directly | Retrieval, citation, groundedness |
| Weakness | Cannot retrieve, cannot be steered by a prompt | Slow; loses acoustics without help |

Neither architecture is sufficient alone, and the report's central argument is
that the choice between them is a genuine engineering trade-off rather than a
matter of one being better.

### 3.1 The acoustic branch

The cascade's fatal flaw — discarding dysfluency — is not inherent to cascades.
It is a consequence of routing *all* information through the transcript. Our
design forks the raw audio instead:

```
                        User audio
                             │
              ┌──────────────┴──────────────┐
              │                             │
      Whisper (text)              Dysfluency analyzer
              │                    (blocks, repetitions,
              │                     prolongations, prosody)
              └──────────────┬──────────────┘
                             │
                   Structured prompt context
```

The analyzer reads the same waveform Whisper reads and emits an
`AcousticProfile` — the events, their durations, and their positions — which the
prompt builder folds back in as structured context. The text pipeline keeps its
retrieval and reasoning; the acoustic evidence survives alongside it.

This is the project's technical contribution, and it works: against a 1400 ms
block spliced into real speech, the analyzer recovered **1480 ms**, located
within 105 ms, while Whisper's transcript contained no trace of it (§9.1).

### 3.2 Measurable value to the user

The acoustic branch is not telemetry. It changes what the product does:

- **The coach slows its own voice.** Fluency load maps onto the TTS rate; a long
  block pulls delivery toward 0.75× normal speed. Matching a struggling
  speaker's tempo is audible in a demo and is the clearest behavioural
  difference from an ordinary assistant.
- **The reply gets shorter** when the acoustic profile shows strain. Someone
  working hard to speak does not want a paragraph back.
- **The timeline overlay** shows the user what the transcript lost, to scale.
- **The progress dashboard** tracks pacing across sessions as a trend.

## 4. System Architecture

### 4.1 Stack

Fixed by the brief, with the placement decisions ours:

| Layer | Technology | Placement |
|---|---|---|
| Frontend | React 18, Vite, TypeScript | Browser |
| Backend | FastAPI, async SQLAlchemy | CPU |
| Live model | Moshi 7B, q4, Mimi codec | **GPU (~6 GB)** |
| STT | faster-whisper `base`, int8 | CPU |
| Acoustic analyzer | wav2vec2 + SEP-28k head | CPU |
| LLM | Qwen2.5-3B-Instruct, GGUF Q4_K_M via llama.cpp | CPU |
| Embeddings | BAAI/bge-small-en-v1.5 | CPU |
| Vector DB | ChromaDB (cosine) | Disk |
| TTS | Kokoro-82M | CPU |
| History | SQLite | Disk |

### 4.2 Fitting an 8 GB card

The deployment target has 8 GB of VRAM, and Moshi alone occupies ~6 GB at q4.
Running the cascade on the GPU as well is not possible.

Rather than swapping models in and out on every mode change — which would add
seconds of stall to each switch — **the entire cascade runs on CPU** and Moshi
stays permanently resident. Every cascade component is small enough for this to
work: Whisper `base` int8, an 82M TTS model, a 33M embedding model, and a 3B LLM
in 4-bit. Knowledge Mode is not latency-critical in the way the live path is, so
the trade is favourable.

### 4.3 Serving the LLM through llama.cpp, not bitsandbytes

The GPU is an RTX 5050 Mobile — Blackwell, compute capability `sm_120` —
requiring CUDA 12.8+ and PyTorch ≥ 2.7. `bitsandbytes` support on `sm_120` is
the most fragile dependency in the stack.

We avoid it entirely on the deployment machine. Fine-tuning happens in the cloud
on T4 hardware (`sm_75`), where bitsandbytes is mature; the merged adapter is
converted to GGUF and served locally by llama.cpp, which has solid Blackwell
support. The application talks to it over an OpenAI-compatible HTTP endpoint, so
swapping the base checkpoint for the fine-tuned one requires no application
change and is a single configuration value (`SCC_LLM_VARIANT`).

### 4.4 Mode routing and the Inner Monologue

Moshi predicts text tokens as a prefix to its audio tokens — its "Inner
Monologue" — and that stream is readable in real time. It is what allows a
native-S2S product to satisfy a text-pipeline design coherently: it feeds
conversation history and signals when a turn needs grounded content.

The user's own words come from Whisper running over a tee of the microphone
audio, off the critical path, since Moshi does not return a transcript of the
user and the analyzer needs that audio regardless.

### 4.5 Degradation

Moshi is the single largest technical risk. The orchestrator therefore treats
the live path as an enhancement, not a dependency: when Moshi is unreachable the
live socket declines with a fallback endpoint, the client reconnects on the
cascade automatically, and the interface tells the user plainly that responses
will take about a second instead of being instant.

This behaviour is covered by automated tests that run with no models present at
all, because that is the state a fresh clone is in and the state the fallback
must survive.

## 5. Conversation Flow and Persistence

Sessions are unified across modes. A conversation can begin on the live path,
hand off to the cascade for a grounded answer, and return, and the turns land in
one ordered thread with each tagged by the mode that produced it.

Each turn persists its transcript, its acoustic profile, its citations, and its
**per-stage timings**. Storing timings per turn rather than only logging them is
what allows latency percentiles to be computed from real sessions instead of a
synthetic benchmark loop.

**Audio is not retained.** Raw waveforms are discarded once analyzed.

## 6. The Knowledge Base

### 6.1 Contents

Five public-domain works on public speaking and vocal delivery, retrieved from
Project Gutenberg — 313,866 words, 1,057 chunks.

| Title | Author | Year | Words |
|---|---|---|---|
| The Art of Public Speaking | J. Berg Esenwein and Dale Carnegie | 1915 | 159,406 |
| Vocal Expression | Katherine Jewell Everts | 1919 | 62,339 |
| How to Become a Public Speaker | William Pittenger | 1900 | 40,181 |
| The Training of a Public Speaker | Grenville Kleiser | 1920 | 31,225 |
| Successful Methods of Public Speaking | Grenville Kleiser | 1920 | 20,715 |

`data/corpus/` is gitignored, so `backend/scripts/fetch_corpus.py` is the
reproducible record: running it recreates the corpus exactly. Full provenance is
in [`data/corpus/SOURCES.md`](../data/corpus/SOURCES.md).

Gutenberg licence boilerplate is stripped before ingestion. Left in, it would be
chunked, embedded, and eventually retrieved as though it were coaching advice.

### 6.2 Scope screening, and what it caught

Period elocution texts sometimes contain chapters on "curing" stammering,
written with attitudes that are both clinical and demeaning by modern standards.
Nothing like that may enter a corpus that feeds coaching answers, so every
download is screened.

The screen looks for **clustering** of pathologising terms rather than raw
frequency. An earlier version also counted "cure", "patient", "treatment" and
"defect", and rejected four of six books — because in 1915 prose those are
ordinary words ("the *treatment* of the subject", "*patient* effort"). It was
measuring period English, not clinical framing.

Because automation can only narrow the field, the script prints the surrounding
text for every hit so a person can judge. **That caught one book.** Clarence
Stratton's *Public Speaking* (1920) passed the automated check but claims:

> *"Many an inveterate stammerer, stutterer, or repeater can be relieved, if not
> cured, of the embarrassing impediment by attention to the position of the
> vocal organs."*

and refers to *"the dumb"*. This is a false cure claim, clinically framed, about
precisely the population this tool serves. Retrieved into an answer it would be
actively harmful. It was excluded, and the exclusion is recorded.

### 6.3 Limitations

These books are a century old. They are genuinely good on the mechanics the
project cares about — pausing, pace, breathing, monotony, stage fright, which
have not changed — but the vocabulary is dated, they predate all modern
evidence-based communication research, and coverage is uneven: over half the
corpus is a single book. This is a **starter corpus**, and supplementing it with
current licensed material is the first item in §12.

## 7. Retrieval-Augmented Generation

### 7.1 Pipeline

Documents are split with LangChain's recursive character splitter into ~512-token
chunks with 64-token overlap, embedded with `bge-small-en-v1.5`, and stored in
ChromaDB with cosine distance and source metadata. Chunk identity is
content-addressed — a hash of source path plus chunk text — so re-ingesting an
unchanged corpus is idempotent.

Query embedding uses bge's instruction prefix on the query side only; applying it
to documents, or omitting it from queries, measurably degrades retrieval.

### 7.2 MMR reranking, implemented rather than delegated

Retrieval fetches 20 candidates and reranks to 4 using Maximal Marginal
Relevance (λ = 0.5), which selects documents that are relevant to the query *and*
unlike those already selected.

We implement MMR directly rather than using the vector store's helper, because
the helper does not return relevance scores and this pipeline needs both: the
scores drive the groundedness gate and the citation display, while the diversity
term prevents four near-identical chunks from one document crowding out the rest
of the corpus.

### 7.3 The groundedness gate

When the best match falls below a threshold, retrieval returns nothing and the
coach says it has no material rather than answering from weak matches.

Calibrating this threshold was the single most instructive piece of engineering
in the retrieval layer, because **it was wrong twice**.

bge embeddings have a high similarity floor: unrelated text does not score near
zero. Our first threshold of 0.28 admitted everything. The second, 0.55, was
derived from a three-document fixture — and once the real 1,057-chunk corpus was
indexed it would have **answered 3 of 8 out-of-corpus questions**, because a
larger corpus offers more chances for a spurious high match.

Measured over the real corpus:

| Set | min | median | max |
|---|---|---|---|
| In corpus (10 questions) | 0.696 | 0.720 | 0.814 |
| Out of corpus (8 questions) | 0.434 | 0.532 | 0.619 |

The final threshold of **0.65** sits in the 0.077 gap, placed deliberately
nearer the out-of-corpus side. The errors are not symmetric: refusing a real
question costs one unhelpful turn, while accepting a false one puts confident
wrong technique advice in front of someone practising their speech. At 0.65,
**0 of 8** out-of-corpus questions are answered and **0 of 10** in-corpus
questions are refused.

The general lesson, and the one worth carrying: **a retrieval threshold is a
property of the corpus and the embedding model together, not a constant.** It
must be re-derived whenever either changes (`backend/scripts/calibrate_gate.py`).

## 8. Prompt Engineering

Four techniques, kept separable so each can be evaluated independently. Every
prompt is versioned, and the version is written onto every turn, so a mid-project
prompt change cannot silently invalidate earlier results.

| Technique | Implementation |
|---|---|
| System prompting | Persona, coaching stance, scope boundary, vocabulary ban |
| Few-shot | Five exemplars teaching behaviours prose cannot pin down |
| Structured | `<acoustic_context>` and `<retrieved_context>` blocks |
| Context-aware | Recent turns plus the acoustic profile of *this* utterance |

### 8.1 The exemplars

Each of the five demonstrates something the system prompt states but which
models routinely violate: responding to content while a long block goes
unmentioned; naming *pace* rather than dysfluency; giving real feedback when it
was explicitly requested; keeping a fluent turn's reply short; and refusing an
assessment request while redirecting to a professional.

### 8.2 A bug the evaluation harness found

Under version `a12-v3` the per-turn coaching directive lived inside the
`<acoustic_context>` block — that is, in the *user* turn. Evaluation showed the
model reciting it back to the speaker:

> *"I think you have a stutter... **The guidance is clear: give them room —
> don't fill the pause or finish their sentence.** I'll help you work on that
> now."*

An instruction about *how to respond* was being delivered as *something to say*.
Anything placed in the user turn reads as content to relay.

Under `a12-v4` the directive moved to the system message, framed explicitly as a
rule. The recitation stopped:

> *"It's not something I can diagnose — that would be for a speech-language
> pathologist to do. Let's start rehearsing instead."*

This is the clearest justification for the versioning discipline: the change is
attributable, reversible, and its effect is visible in the evaluation output.

## 9. Measurements

All figures reproducible from `backend/scripts/` — see that directory's README
for the exact commands.

### 9.1 The acoustic branch recovers what the transcript loses

A dysfluent utterance was constructed from real synthesized speech with known
ground truth — `"I ... I ... I want [1400 ms block] water please"` — and run
through the production path.

Whisper's transcript: **`"I, I, I, I, want. Water please."`** The block is
absent from it entirely.

| Ground truth | Measured | Error |
|---|---|---|
| Block duration 1400 ms | 1480 ms | 80 ms |
| Block onset at 1345 ms | 1240 ms | 105 ms |
| Word repetition | detected ×3 | — |
| TTS rate response | 0.75× | — |

The error bound is set by Whisper's word-timestamp resolution, not by the
analyzer.

### 9.2 Whisper model selection

Chosen on **word-timestamp accuracy**, not transcript quality, because the
acoustic branch derives block duration from the timings.

| Model | STT p50 | Real-time factor | Block measured | Error |
|---|---|---|---|---|
| `tiny` | 285 ms | 0.08 | **missed entirely** | — |
| **`base`** | **598 ms** | **0.17** | 1440 ms | **40 ms** |
| `small` | 2025 ms | 0.56 | 1460 ms | 60 ms |

`base` is 3.4× faster than `small` and marginally *more* accurate on the
measurement that matters, so it is the default. `tiny` is disqualified: it
smooths disfluent speech into fluent speech, which is the failure this entire
project exists to avoid.

### 9.3 Cascade latency

Warm path, CPU, 3.6 s utterance:

| Stage | p50 |
|---|---|
| Whisper `base` STT | ~600 ms |
| Acoustic analyzer | 3 ms |
| Prompt assembly | <1 ms |
| **Time to first audio** | **~1.9 s** |

**Our original estimate of 750 ms – 1.1 s was wrong**, and measurement
disproved it. The figure above was taken with a 0.5B model where the project
ships 3B, so the real number is higher still.

This does not weaken the argument — it widens the gap against Moshi's ~200 ms,
which is the comparison the project exists to make.

## 10. Model Evaluation

### 10.1 Why behavioural checks, not only ROUGE

There is no single correct reply to *"I have an interview tomorrow"*. A dozen
good coaching answers share almost no n-grams with each other or with a
reference, so a high ROUGE score against one reference mostly measures luck.

Our primary metric is therefore a set of **deterministic behavioural checks**,
each mapped to a specific rule in the system prompt or in `docs/ETHICS.md`: did
the model follow the instructions it was given? This converts a vague claim into
a probeable number.

The eval set is 25 held-out cases spanning long blocks, repetitions, fillers,
fast pace, fluent speech, explicit feedback requests, diagnosis requests,
knowledge questions, out-of-corpus questions, and emotionally loaded turns.
**These cases must not appear in the fine-tuning data**; if they do, the
comparison measures memorisation.

The harness imports the production prompt module rather than reimplementing it —
a comparison run on a different prompt than the product uses proves nothing about
the product.

### 10.2 Sampling

`--runs` must be 5 or more for any quoted figure. At temperature 0.6 a single
pass leaves categories with n = 2–3, where one unlucky generation swings a rate
by tens of points. Observed during development: *"did not name the dysfluency
unprompted"* read **77%** over one run and **95%** over five. The single-run
figure was noise and would have produced the wrong conclusion about a prompt
change.

### 10.3 `[TRACK M]` Baseline and comparison

> **What goes here.** Run:
> ```
> python ml/evaluation/run_eval.py \
>   --base-url http://127.0.0.1:8080/v1 --base-model  qwen-base \
>   --tuned-url http://127.0.0.1:8081/v1 --tuned-model qwen-tuned --runs 5
> ```
> Paste the table from `ml/evaluation/results/report.md`, then add ROUGE-L,
> BERTScore, an LLM-as-judge rubric (empathy, actionability, pacing
> appropriateness), and the human study (n = 10–15, Likert).
>
> **Also state which base checkpoint produced it.** The baseline currently
> recorded in `ml/evaluation/README.md` used Qwen2.5-**0.5B** as a stand-in to
> validate the harness, not the 3B the project ships.
>
> **The qualitative finding matters as much as the table.** An examiner will ask
> what the base model does *wrong*. From the 0.5B stand-in: it complied with 40%
> of diagnosis requests — one reply was *"I think you have a stutter, and it's
> coming from the silent block"* — referred to a professional only 13% of the
> time, and invented an answer half the time when given no retrieved material.
> Style was already near-perfect. If the 3B base behaves similarly, fine-tuning
> should be judged on **scope**, not on tone.

### 10.4 `[TRACK M]` Fine-tuning

> **What goes here.** QLoRA configuration (rank, alpha, target modules, 4-bit
> NF4 base), the training hardware (Colab T4 / Kaggle 2×T4), hyperparameters,
> epochs, loss curves, and the merge-to-GGUF conversion.
>
> State explicitly that training ran in the cloud on `sm_75` and only inference
> runs locally, and why (§4.3).

### 10.5 `[TRACK M]` Dataset

> **What goes here.** How the 2–5k coaching instruction pairs were generated,
> the curation and filtering process, provenance, and the held-out split.
> Confirm the §10.1 eval set was excluded from training.
>
> Per §10.3, weight the dataset toward scope failures — declining assessment,
> referring onward, admitting when there is no material — rather than toward
> style, which the base model already handles.

### 10.6 `[TRACK M]` Optimization

> **What goes here.** At least one technique with before/after measurements:
> model size on disk, peak VRAM, tokens per second, time to first token, and the
> quality delta between FP16 and Q4_K_M on identical prompts. QLoRA plus 4-bit
> quantization satisfies the requirement; report both.

### 10.7 `[TRACK M]` Native S2S vs. cascade

> **What goes here.** The project's headline quantitative result: Moshi against
> the cascade on latency (p50/p95 over ≥50 turns), dysfluency perception
> fidelity, and response groundedness.
>
> Our cascade figure is **~1.9 s to first audio** (§9.3). Report Moshi's
> measured latency beside it, and be explicit about what each architecture buys
> and what it costs.

## 11. Limitations

Stated plainly, because an examiner will find them.

1. **The live path is unproven.** The Moshi client is written and the
   application falls back cleanly, but the flagship has not been exercised
   end to end; it depends on the model service and on Blackwell `sm_120`
   bring-up.
2. **The analyzer in the current build is a heuristic**, derived from Whisper
   word timings and frame energy. It was built so the interface and prompt
   contract were not blocked on SEP-28k acquisition. It detects four of the five
   event classes — not sound repetitions, which need sub-word resolution — and
   **no figure in this report attributed to the trained classifier may come from
   it**. Every profile records which backend produced it.
3. **English only.** Moshi is English-only, and the corpus and prompts are
   English.
4. **The corpus is a century old** (§6.3).
5. **The cascade is slow** (§9.3), and the honest figure is seconds.
6. **The heuristic analyzer is unvalidated against clinical ground truth**, by
   design — validating it would require exactly the clinical framing the project
   excludes. Its accuracy is reported against spliced ground truth only.
7. **No user study with the target population.** Everything reported is
   technical measurement.

## 12. Future Improvements

1. **Replace the starter corpus** with modern, licensed communication-coaching
   material, and re-derive the groundedness gate against it.
2. **Deploy the trained SEP-28k classifier** and re-run §9.1; the error bound
   should tighten.
3. **A LoRA adapter for Moshi.** Moshi cannot be system-prompted, so the
   coaching persona on the live path has to come from fine-tuning the backbone.
4. **Streaming STT** to cut the ~600 ms transcription cost by transcribing
   during speech rather than after it.
5. **A user study** with people who experience speech differences — the only way
   to learn whether the pacing behaviour actually helps.
6. **Multilingual support**, gated on a multilingual S2S model existing.

## 13. Conclusion

The project set out to test a specific claim: that conventional voice assistants
discard the information most relevant to helping someone practise speaking, and
that a system can be built which does not.

The claim held, and it is measurable. Whisper `tiny` erased a 1.4-second block
and three repetitions from our test utterance; the parallel acoustic branch
recovered that block to within 80 ms and changed the coach's own speaking rate in
response.

The engineering answer is not to abandon the cascade. Native speech-to-speech
buys conversational immediacy — ~200 ms, full-duplex, dysfluency perceived
directly — and gives up retrieval, citation, and steerability. The cascade buys
grounded, attributable answers and pays seconds for them. The product runs both
and routes between them, and the acoustic branch ensures the text path does not
discard the evidence the project is about.

Two of the more useful findings were corrections to our own work. The
groundedness threshold was wrong twice, and only measuring against the real
corpus revealed that a retrieval threshold is a property of the corpus and the
embedding model together, not a constant. And the coaching directive, placed in
the user turn, was recited back to the user until it was moved into the system
role. Both were caught because the system was measured rather than assumed to
work.

---

## Appendices

- **A — Ethics and scope statement:** [`docs/ETHICS.md`](ETHICS.md)
- **B — Corpus provenance and the excluded source:** [`data/corpus/SOURCES.md`](../data/corpus/SOURCES.md)
- **C — Verification scripts and measurements:** [`backend/scripts/README.md`](../backend/scripts/README.md)
- **D — Evaluation harness and baseline:** [`ml/evaluation/README.md`](../ml/evaluation/README.md)
- **E — Demo walkthrough:** [`docs/DEMO.md`](DEMO.md)
- **F — Architecture and task plan:** [`docs/PROJECT_PLAN.md`](PROJECT_PLAN.md)
- **G — Repository:** https://github.com/Ali-Khamis45/we-s2s-finalproject
