# Speech Confidence Coach — defence pack

Companion to `Speech-Confidence-Coach.pptx`. 27 core slides, 16 appendix slides,
30 minutes, two presenters.

Full speaker scripts live in the deck's own speaker-notes pane — one per slide,
each carrying objective, what appears, speaker, timing, the script, technical
backup, and the transition line. This document is everything around them.

| | |
|---|---|
| **Deck** | `Speech-Confidence-Coach.pptx` — 43 slides, 16:9 (13.33″ × 7.5″) |
| **Core talk** | slides 1–27, exactly 30:00 |
| **Appendix** | slides A1–A16, not part of the 30 minutes |
| **Presenter 1** | Ali — slides 1–13, light mode, 14:45 |
| **Presenter 2** | Youssef — slides 13–27, Night Studio, 15:15 |
| **Repository** | github.com/Ali-Khamis45/we-s2s-finalproject |

---

## 1 · Design system

Taken from `frontend/src/styles/tokens.css`, not invented. The deck is the
application's own two themes.

**Night Studio (Presenter 2)** — a dark recording booth lit by one warm lamp.
Grounds `#0A0F0D` `#0E1512` `#141D19` `#1B2621`; rules `#223029` `#33463D`;
type `#EFF2F1` `#DCE5E4` `#8CA096` `#5D7268`; the lamp `#F9BF29` `#FFD35C`
`#B8891A`; the grounded path `#6F9C8A` `#3B5D50`.

**Light (Presenter 1)** — the same room, re-lit. Grounds `#E4E9E8` `#EFF2F1`
`#FFFFFF` `#F5F7F6`; rules `#DCE5E4` `#C2CFCC`; type `#2F2F2F` `#4A4A4A`
`#6A6A6A` `#9AA5A2`; amber darkened for legibility `#8A6410` `#A2760F`;
sage `#3B5D50`.

**Event colours, both themes** — block `#2D90C3`, prolongation `#C78200`,
sound repetition `#BF6D9B`, word repetition `#007C54`, interjection `#B64100`,
unsure neutral. These are Okabe-Ito derivatives re-placed inside an OKLCH
lightness band and validated all-pairs for colour-vision deficiency; they are
categorical, never a severity ramp.

**Backgrounds** are generated, not stock: a warm radial lamp fall from the
upper left, a sage fall lower right, a vignette to `--ground-0`, and 3% grain —
the same three layers `atmosphere.css` composes in the running app.

**Type.** The product sets Bricolage Grotesque, Instrument Sans, JetBrains Mono
and Instrument Serif. None of those ship with Office, so the deck substitutes
metric-safe equivalents that render identically on any machine: **Arial** for
display, **Calibri** for body, **Courier New** for data and labels, **Cambria
italic** for the serif accent (the word *Confidence* in the wordmark, and the
emphasis words in titles).

**The repeated mark** is the app's mic orb — an amber disc with a faint ring.
It appears at the foot of every slide beside the slide number, oversized on the
title, and dimming across the handoff. There are no accent stripes or header
bars anywhere in the deck.

---

## 2 · Slide-by-slide storyboard

Theme column: **L** = light, **D** = Night Studio, **L→D** = the dissolve.

### Act I — why this problem is different

| # | Title | Who | Theme | Time | Purpose | Visual composition | Build / animation |
|---|---|---|---|---|---|---|---|
| 1 | Speech **Confidence** Coach | P1 | L | 0:35 | Land the thesis in one sentence; establish two authors, two tracks | Wordmark set as the app sets it; oversized mic orb right; capability chips; two presenter cards | None. Let it sit. |
| 2 | Speaking is not optional | P1 | L | 1:10 | The human problem as a *practice* problem, not a knowledge problem | Standfirst, then three numbered cards | Cards appear 1 → 2 → 3 on click |
| 3 | Voice AI begins by **deleting the evidence** | P1 | L | 1:15 | Make the information loss visible in three seconds | **The key visual.** The real utterance drawn to scale on a 3.2 s timeline — green repetition 0–0.75 s, blue block 1.20–2.70 s — then the two transcripts below | Timeline track first, then the green bar, then the blue bar, then the two transcript cards. Four clicks. |
| 4 | Chosen on timestamp accuracy, not transcript quality | P1 | L | 1:10 | A component chosen by measurement, where the obvious metric gives the wrong answer | Three-row comparison, `base` row highlighted amber | Rows top-down; the amber highlight lands last |
| 5 | A coach that hears *how* something was said | P1 | L | 1:10 | Convert the claim into product behaviour before any architecture | Four orb-marked effects left; real timeline screenshot right | Effects appear in order; screenshot fades in with the first |
| 6 | An accessibility tool. Deliberately not a medical one. | P1 | L | 1:00 | Pre-empt the ethics question; show the boundary is engineered | Two cards: is / is not, and the five enforcement points; vocabulary strip beneath | Left card, then the five points 1→5 |
| 7 | Fork the audio *before* the transcript is the only witness | P1 | L | 1:20 | The central technical insight, in one diagram | USER AUDIO → split → Whisper / analyzer → structured context | **Progressive, five clicks:** audio box → split lines → Whisper → analyzer → merge to context. Do not show it whole. |
| 8 | Two paths over the same audio | P1 | L | 1:25 | Both paths side by side with honest measured latency | Amber column (live) and sage column (grounded), each with a large latency figure | Live column, then grounded column |
| 9 | One decision, both the strength and the limitation | P1 | L | 1:25 | Defend the dual architecture as a genuine trade-off | Facing cards — what removing the text step buys / costs — with the resolution beneath | Buys card, then costs card, then the resolution block |
| 10 | One frozen contract between the two tracks | P1 | L | 1:15 | Give the acoustic branch the weight of a contribution | Three cards: what it measures (in event colours), the dominance correction, what it drives | Left, middle, right |
| 11 | The whole system, once | P1 | L | 1:20 | One complete map, only after each idea is understood | Two lanes under shared audio, over shared persistence | **Progressive:** audio → live lane → grounded lane box by box → shared state bar |
| 12 | Calm on purpose | P1 | L | 1:10 | The interface as a response to the user's emotional state | Real light-theme screenshot left; three engineering notes right | Screenshot, then notes 1→3 |
| 13 | We have seen what it does → Now let us look inside | P1→P2 | **L→D** | 0:45 | Make the speaker change feel like the product changing state | Light dissolving into Night Studio across the slide; orbs dimming left to right; each presenter's half on their own side | The dissolve is the slide. If the live demo is on a second screen, switch its theme here too. |

### Act II — inside the engine

| # | Title | Who | Theme | Time | Purpose | Visual composition | Build / animation |
|---|---|---|---|---|---|---|---|
| 14 | From microphone to *intelligence* | P2 | D | 0:40 | Reset attention on the speaker change; lay out the route | Statement of intent, then eight stage cards | Cards in two rows of four |
| 15 | Moshi runs. And it is **ten times slower** than we claimed. | P2 | D | 1:15 | Correct the project's own headline number, on our terms | Four-bar latency ladder on one scale; our plan's own instruction quoted beside it; three framing notes | Bars top-down — published figure, M1, M2 p50, M2 p95 — then the quote |
| 16 | Whisper base, int8, on CPU | P2 | D | 1:00 | Show the STT choice has downstream consequences | What the transcript carries / why timings matter; the four thresholds | Two cards, then the threshold band |
| 17 | Two backends behind one interface | P2 | D | 1:15 | The trained classifier as a measured deliverable, kept distinct from the heuristic | Heuristic card / wav2vec2 card; per-class F1 bars in the app's event colours | Heuristic, then wav2vec2, then the bars growing left to right |
| 18 | Look it up first, then answer | P2 | D | 1:10 | Teach RAG intuitively, then ground it in this pipeline | Five-step pipeline; three "why" cards beneath | **Progressive:** steps 01→05, then the why cards together |
| 19 | We got this threshold wrong *twice* | P2 | D | 1:20 | Refusal as a designed safety property, with the calibration story | Similarity axis with both question sets as bands, the 0.077 gap, the marker at 0.65 | **Four clicks:** axis → out-of-corpus band → in-corpus band → the marker dropping into the gap |
| 20 | Train in the cloud at 16-bit. Serve on a laptop at **4-bit**. | P2 | D | 1:10 | Fine-tuning and quantization as one deployment chain | Four-stage chain; size bars f16 vs Q4_K_M; the bitsandbytes argument | Chain left to right, then the size bars |
| 21 | The fine-tune improved style and **regressed safety** | P2 | D | 1:25 | **The required comparison, delivered as a real result including the regression** | Six checks with base, tuned and a two-way delta bar; the re-scoring beside it; what it does, why, what follows | Style rows first, then the two safety rows, then the re-scoring card. Let the two red rows land. |
| 22 | Two modes, **one 8 GB card** | P2 | D | 1:00 | The constraint that explains placement — and M12's correction to our own framing | A VRAM bar almost entirely full, the CPU stack beside it, the finding underneath | The bar filling to 7.7 GB is the moment |
| 23 | Practice transcripts are personal | P2 | D | 1:00 | Turn a security checklist into one argument | Three columns: FastAPI, the two-token design, the remaining surface | Column by column |
| 24 | The biggest number in this project was a **build flag** | P2 | D | 1:20 | The largest measured effect, and the trap that hid it | Three serving builds as bars on one scale; the CUDA 12.4 trap; the conditional conclusion | CPU bar, then CUDA 12.4 (note it is *longer*), then 13.3 |
| 25 | What we measured, and what broke when we checked | P2 | D | 1:00 | Engineering discipline through two real defects | Four counters, then two defects as symptom → resolution | Counters, then defect 1, then defect 2 |
| 26 | What is built, and what is *not* | P2 | D | 0:55 | An unmissable line between complete and outstanding work | Sage column (done) and amber column (outstanding), side by side | Left column, then right. Do not rush the right column. |
| 27 | Speech AI should not only understand *what* was said | P2 | D | 0:30 | Close the argument; hand to questions | The thesis large; three closing findings; Questions placed quietly | Statement, then the three cards |

### Appendix — A1 to A16, on demand

| # | Title | Use it when |
|---|---|---|
| A1 | Technology decision matrix, part 1 | "Justify your whole stack" |
| A2 | Decision matrix, part 2 | as above |
| A3 | API surface and the generated contract | "How big is the backend, and how do you stop the frontend drifting?" |
| A4 | Persistence schema | "What exactly do you store?" |
| A5 | Refresh rotation and reuse detection | "Walk me through authentication" |
| A6 | WebSocket protocol | "How does a turn actually travel?" |
| A7 | Retrieval mathematics | "Show me the MMR formula" / "Where did each constant come from?" |
| A8 | Gate calibration, in full | "How did you pick 0.65?" |
| A9 | Demoting drills instead of deleting them | "Give me an example of a non-obvious fix" |
| A10 | Benchmark methodology | "Why is your test fixture synthetic?" |
| A11 | Hardware budget | "Why is everything on CPU?" |
| A12 | **M9 in full — all eleven checks, base vs fine-tuned** | anything about the comparison or the regression |
| A13 | Testing matrix, and the ethics boundary | "How do you know it works?" / "Is this medical?" |
| A14 | Anticipated questions | your own cheat sheet — 18 questions with one-line answers |
| A15 | **M12 — the comparison on three axes** | "What did the headline comparison actually find?" |
| A16 | **Latency, stage by stage** | "Where does the time go?" / the two prompt optimizations |

## 3 · Timing table, with the arithmetic

### Presenter 1 — light mode

| Slide | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 | 13 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Seconds | 35 | 70 | 75 | 70 | 70 | 60 | 80 | 85 | 85 | 75 | 80 | 70 | 30 |
| Running | 0:35 | 1:45 | 3:00 | 4:10 | 5:20 | 6:20 | 7:40 | 9:05 | 10:30 | 11:45 | 13:05 | 14:15 | **14:45** |

`35+70+75+70+70+60+80+85+85+75+80+70+30 = 885 s = 14:45`

### Presenter 2 — Night Studio

| Slide | 13 | 14 | 15 | 16 | 17 | 18 | 19 | 20 | 21 | 22 | 23 | 24 | 25 | 26 | 27 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Seconds | 15 | 40 | 75 | 60 | 75 | 70 | 80 | 70 | 85 | 60 | 60 | 80 | 60 | 55 | 30 |
| Running | 15:00 | 15:40 | 16:55 | 17:55 | 19:10 | 20:20 | 21:40 | 22:50 | 24:15 | 25:15 | 26:15 | 27:35 | 28:35 | 29:30 | **30:00** |

`15+40+75+60+75+70+80+70+85+60+60+80+60+55+30 = 915 s = 15:15`

**Total 885 + 915 = 1800 s = 30:00 exactly.**

Slide 13 is shared: Presenter 1 delivers 30 seconds, then the theme changes and
Presenter 2 delivers 15. Presenter 2 should be speaking at the 15:00 mark.

**If you are running long.** Compress slides 2 and 12 first (they carry the
least unique evidence), then 16 and 23. Never compress 3, 8, 15, 19, 21, 24 or
26 — those are the seven slides an examiner will grade you on, and 21 is the
single most important slide in the deck.

**If you are running short.** Pull A12 (M9 in full), A15 (M12's three axes) or
A16 (latency stage by stage) forward — each extends an argument already on
screen.

---

## 4 · Technology decision matrix

| Technology | Role here | Why this one | Main benefit | Main trade-off | Runs on |
|---|---|---|---|---|---|
| Moshi 7B q8 | Native S2S live coach | Only quantized native S2S that builds on Blackwell; perceives dysfluency from audio tokens | No text bottleneck; full duplex with barge-in | No prompt surface, no retrieval, English only; 2.0 s measured, not 200 ms | GPU, resident |
| Mimi neural codec | Audio ↔ audio tokens | Moshi's own codec, 12.5 Hz framing | Makes token-level audio modelling possible | Causal encoder lookahead sets a latency floor at some multiple of 80 ms | GPU |
| faster-whisper `base` int8 | Speech to text plus word timings | Chosen on timestamp accuracy: 40 ms error vs `small`'s 60 ms, and 3.4× faster | Word boundaries accurate enough to measure a block | Still costs ~600 ms; `tiny` is disqualified outright | CPU |
| wav2vec2 + SEP-28k head | Dysfluency classification | Five-class multi-label; frozen feature extractor fits a free T4 | Macro F1 0.638, including sound repetitions the heuristic cannot reach | Podcast speech under-represents severe dysfluency and skews adult | CPU |
| Heuristic analyzer | Fallback and scaffold | No weights, no torch — unblocks the interface and the prompt contract | The product works on a fresh clone with no models | Four of five classes; no sound repetitions | CPU |
| bge-small-en-v1.5 | Embeddings | 33M parameters, strong for its size, query-side instruction prefix | Semantic retrieval at negligible cost | High similarity floor, which forced a calibrated gate | CPU |
| ChromaDB | Vector store | Local, persistent, cosine, carries source metadata | Every citation can name its book and position | Index format is version-sensitive — see `FIX_LOG` §3 | Disk |
| MMR, λ = 0.5 | Reranking 20 → 4 | Implemented directly, because the store's helper returns no relevance scores | Scores feed the gate and the citation display; diversity stops one book dominating | O(k·n) per query, fine at n = 20 | CPU |
| Qwen2.5-3B-Instruct | Grounded reply generation | Largest instruct model that decodes usefully on CPU in 4-bit | Runs entirely offline on a laptop | An 8B stays tighter on the same prompt; 3B is a hardware trade | CPU |
| QLoRA (r=16, α=32) | Fine-tuning | 4-bit NF4 base with trainable adapters fits a free T4 | A 3B fine-tune on free hardware | 400 pairs, not the specified 2–5k | Colab T4 |
| GGUF Q4_K_M | Quantization | 5886 MB → 1835 MB, 3.2× | The model becomes a file you can ship | Lossy; the FP16-vs-Q4 quality delta is unmeasured | Disk / CPU |
| llama.cpp / llama-server | Model serving | Solid Blackwell support; avoids bitsandbytes on `sm_120` entirely | Swapping checkpoints is one config value, no code change | An extra process and an HTTP hop | CPU |
| Kokoro-82M | Text to speech | 82M parameters, and it exposes a speed parameter | The speed parameter is how the acoustic branch reaches the output | English voices only | CPU |
| FastAPI | API and orchestration | Async for a workload that is mostly waiting; OpenAPI generated from Pydantic | Both sockets and 24 routes share one orchestrator | Single-process; no horizontal scaling story | CPU |
| Async SQLAlchemy + SQLite | History and per-turn metrics | One file, no server, correct for a single-user local product | Percentiles computed from real sessions, not a benchmark loop | Would need Postgres for concurrent multi-user | Disk |
| React 18 + TypeScript + Vite | Frontend | Real-time stateful interface; API types generated from OpenAPI | A backend field rename breaks the build, not the runtime | Client-side only; no SSR | Browser |
| JWT + rotating opaque refresh | Authentication | Access and refresh are deliberately different kinds of thing | All revocation lives server-side; reuse revokes the whole family | A JWT still cannot be un-issued within its 10 minutes | CPU |

---

## 5 · Evidence map

Where every substantive claim on a slide comes from.

| Slide | Claim | Source in the repository |
|---|---|---|
| 3 | Block of 1400 ms recovered at 1500 ms; onset 1345 → 1200 ms; utterance 3157 → 3158 ms; repetition detected ×2 | `backend/scripts/README.md` (verify_acoustic_branch section); root `Readme.md` |
| 3 | Whisper `base` transcript `"I, I, I, want Water"`; `tiny` transcript `"I want water please"` | `backend/scripts/README.md`; `docs/REPORT.md` §1, §9.2 |
| 4 | tiny 285 ms / missed · base 598 ms / 1440 ms / 40 ms · small 2025 ms / 1460 ms / 60 ms; RTF 0.08 / 0.17 / 0.56 | `backend/scripts/bench_whisper.py` output recorded in `backend/scripts/README.md` and `backend/app/core/config.py` |
| 5 | TTS rate floor 0.75×, ceiling 1.15×; reply shortens under strain | `config.py` (`tts_speed_min/max`); `schemas/acoustic.py` (`suggested_speech_rate`); `prompts/templates.py` (SYSTEM_PROMPT) |
| 6 | Vocabulary ban; five enforcement points | `docs/ETHICS.md`; `prompts/templates.py`; `prompts/exemplars.py`; `backend/scripts/fetch_corpus.py`; `ml/evaluation/checks.py` |
| 6 | The excluded book (Stratton, *Public Speaking*, 1920) | `data/corpus/SOURCES.md`; `docs/REPORT.md` §6.2 |
| 7 | The fork; analyzer adds ~3 ms | `backend/app/services/orchestrator.py` (`answer_audio`) |
| 8, 15 | Moshi p50 2009.9 ms / p95 2645.0 ms through the M2 production bridge | `docs/PROJECT_PLAN.md`, Track M table, row M2 |
| 8, 15 | Moshi p50 1636.2 / p95 1808.0 through M1's relay, reconfirmed at 1600.8/1775.9 and 1606.3/1760.4 | `docs/M1_BRINGUP_LOG.md` §"Measured latency" |
| 15 | "do not claim ~200 ms for Moshi in the report" | `docs/PROJECT_PLAN.md`, M1 update note |
| 15 | Mimi at 12.5 Hz, 80 ms/frame, causal lookahead | `docs/M1_BRINGUP_LOG.md` |
| 15, 21 | q8 weights (`kyutai/moshiko-candle-q8`); no q4 config upstream | `docs/M1_BRINGUP_LOG.md` §"Weight variant used" |
| 16 | BLOCK_GAP_MS 450 · PROLONGATION_MS 380 / 4 chars · repetition window 700 ms | `backend/app/services/dysfluency.py` |
| 16 | Endpointer silence 700 ms | `backend/app/services/vad.py` |
| 17 | SEP-28k: 20,170 clips, four shows, 254/256 episodes | `docs/PROJECT_PLAN.md`, row M3 |
| 17 | Macro F1 0.638; Block .616 · Prolongation .574 · SoundRep .621 · WordRep .628 · Interjection .751 | `ml/dysfluency/reports/metrics.md` and `metrics.json` |
| 17 | Epoch 3 was the peak: 0.6405 → 0.6325 → 0.6273 | `docs/PROJECT_PLAN.md`, row M4 |
| 18 | 5 books, 313,866 words, 1,057 chunks; ~512-token chunks, 64 overlap | `data/corpus/SOURCES.md`; `config.py`; `services/ingestion.py` |
| 18 | Query-side instruction prefix only | `services/retrieval.py` (`embed`) |
| 18 | MMR implemented directly because the store's helper returns no scores | `services/retrieval.py` module docstring |
| 19 | In corpus 0.696 / 0.720 / 0.814; out 0.434 / 0.532 / 0.619; gap 0.077; threshold 0.65 | `backend/scripts/calibrate_gate.py`; `config.py` comment above `retrieval_min_score` |
| 19 | 0/8 answered, 0/10 refused | same, plus the re-run in `docs/FIX_LOG.md` |
| 19 | 0.28 admitted everything; 0.55 would have answered 3 of 8 | `config.py`; `docs/REPORT.md` §7.3 |
| 20 | 400 pairs from 833 candidates, 433 rejected, 48% acceptance | `docs/PROJECT_PLAN.md`, row M6 |
| 20 | QLoRA r=16, α=32, 4-bit NF4, 3 epochs, Colab T4 | `docs/PROJECT_PLAN.md`, row M7; `adapter_config.json` |
| 20 | 5886 MB → 1835 MB (~3.2×); ~22 tok/s generate, ~55 tok/s prompt | `docs/PROJECT_PLAN.md`, row M8; `ml/finetuning/M8_README.md` |
| 20, 21 | RTX 5050 Mobile, Blackwell `sm_120`, CUDA 12.8+, PyTorch ≥ 2.7 | `docs/REPORT.md` §4.3; `docs/PROJECT_PLAN.md` |
| 21 | ~7.7 GB of 8.15 GB used by Moshi q8, warm | `docs/M1_BRINGUP_LOG.md` §"Measured latency", GPU state |
| 22 | argon2id t=3 m=64 MB p=4; access token 10 min; refresh 32 bytes, sha256, rotated, family reuse detection | `backend/app/services/auth.py`; `config.py` |
| 22 | WS tickets single-use, 30 s, burned before any audio is read; close 4401 | `backend/app/api/ws_auth.py`; `docs/PROTOCOL.md` |
| 22 | Another owner's session returns 404 | `services/orchestrator.py` (`get_or_create_session`) |
| 23 | Spoken no-retrieval 776 ms + 9.0 s = 9.8 s; typed with retrieval 25 ms + ~15 s; cold first turn ~21 s | `docs/REPORT.md` §9.3 |
| 23 | 28.11 s observed, of which 23.58 s generate | `docs/screenshots/04-waterfall-dark.png` |
| 23 | Excerpt cap: 2,023 of 3,167 tokens (64%) → 1,666 tokens; generation cap 420 → 200 | `config.py` comments above `max_excerpt_chars` and `llm_max_tokens`; `docs/REPORT.md` §9.3 |
| 24 | 92 backend, 42 frontend, tsc clean, build clean | `docs/FIX_LOG.md` §"Verification" (run of 2026-09-07). 87 test functions; four files parametrize, so 92 cases are collected |
| 22, A3 | 25 HTTP paths, 30 operations, 2 WebSockets | `docs/openapi.json` (25 paths, 30 methods) plus `/ws/live` and `/ws/knowledge` |
| 24 | Unreadable Chroma index reported as empty; `KeyError: '_type'`; `CorpusUnreadableError`, `corpus_status` | `docs/FIX_LOG.md` §3; `core/errors.py`; `services/retrieval.py` |
| 24 | 56 drill chunks, 5.3%; 25% of flags are majority prose; penalty 0.15; gate unmoved | `docs/FIX_LOG.md` §"reply quality" cause 3; `services/ingestion.py`; `config.py` |
| 17, 26, A15 | Fidelity: macro F1 0.612 over 400 held-out SEP-28k clips, reproducing M4's 0.638 | `docs/M12_COMPARISON.md` §2; `ml/evaluation/bench_fidelity.py` |
| 19, A15 | Groundedness: 100% in-corpus, 0% out-of-corpus, including the medication and diagnosis questions | `docs/M12_COMPARISON.md` §3; `ml/evaluation/bench_groundedness.py` |
| 20, A11 | Q4_K_M: 5892 → 1840 MB (3.20×), 69.2 tok/s prefill, ~20 decode, 660 ms TTFT, 3.33 GB peak RSS | `docs/M10_OPTIMIZATION.md` §1; `ml/evaluation/bench_optimization.py` |
| 20 | Base and fine-tuned identical in every cost dimension (adapters merged, not runtime) | `docs/M10_OPTIMIZATION.md` §1 |
| 21, A12 | All eleven M9 checks; 125 generations per model; 0 exact contamination, max fuzzy 0.58 | `docs/M9_COMPARISON.md` |
| 21, A12 | The regression survives broadened re-scoring: 73% vs 40% | `docs/M9_COMPARISON.md` §"This was verified" |
| 21 | ETHICS.md discloses the regression | `docs/ETHICS.md`, model-limitations list |
| 22, 24, A15, A16 | Serving builds: CPU ~57 s, CUDA 12.4 ~61 s, CUDA 13.3 ~1.4 s — 43× | `ml/finetuning/llama.cpp/README.md`; `docs/M10_OPTIMIZATION.md` §4; `docs/M12_COMPARISON.md` §1 |
| 22, 24 | The two modes contend for one 8 GB GPU; that governs latency, not model speed | `docs/M12_COMPARISON.md` §1 and §"What this means" |
| 26 | M11 deferred with four verified blockers | `docs/M11_MOSHI_LORA.md` |
| 26 | Retrieval currently degrades reply quality vs retrieval off | `docs/FIX_LOG.md` §"reply quality" cause 2 |

---

## 6 · Do not present these as complete

Everything in this section is either unfinished, uncertain, or contradicted
somewhere in the repository. The deck already handles each one — this list is
so you do not accidentally undo that under questioning.

### Required work: all four evaluation deliverables are now done

Merged into `master` on 2026-09-07 from `feat/m9-m12-evaluation`. If you are
working from an older copy of this pack, this section replaced a list that said
they were outstanding.

| Item | State | Where the deck presents it |
|---|---|---|
| **M9 — base vs fine-tuned** | ✅ Done. 125 generations per model, both at Q4_K_M. Style improved; **both safety-critical checks regressed** (diagnosis refusal 53%→20%, referral 60%→33%), verified against broadened scoring. | Slide 21, in full; A12 |
| **M10 — optimization analysis** | ✅ Done. 3.20× compression, 69/20 tok/s, 660 ms TTFT, 3.33 GB RSS. Base and fine-tuned identical at inference. | Slide 20; A11 |
| **M11 — Moshi LoRA** | ⏸️ Deferred, with four blockers **verified rather than assumed**. It is the plan's one explicit stretch goal, not a rubric item. | Slide 26, right column |
| **M12 — the headline comparison** | ✅ Done. Latency, fidelity and groundedness across both paths, with two axes honestly returning "not measurable" for Moshi. | Slides 22 and 24; A15 |

### What is genuinely still outstanding

| Item | Why it matters |
|---|---|
| **The M9 regression is unmitigated** | The fine-tuned model should not ship as the default. Three remedies are written down in `docs/M9_COMPARISON.md`; none has been applied. Say this yourself on slide 26 — it is the single most likely follow-up question. |
| **A controlled f16-vs-Q4_K_M quality delta** | M9 compares base against fine-tuned at *fixed* precision, so it isolates fine-tuning, not quantization. Needs an f16 arm, roughly two hours per model on CPU. |
| **The two modes under simultaneous GPU load** | Measured separately, never head-to-head — which is precisely the contention M12 identifies as the real constraint. |
| **A user study** | Everything reported is technical measurement. Nobody from the target population has used the system. |
| **The corpus** | Edwardian throughout, and retrieval measurably degrades reply quality against retrieval-off. A corpus problem, not a code problem. |

### Figures the repository contradicted itself on — now corrected

All of these were found while building this deck and fixed in the repository on
2026-09-07 (commit `3901531`). The deck already carried the measured value; the
documents now agree with it. Kept here so you can answer if an examiner is
holding an older copy of the report.

| Conflict | What it says now | What it said before |
|---|---|---|
| **Moshi latency** | p50 2009.9 ms / p95 2645.0 ms through the M2 production bridge (`PROJECT_PLAN.md` row M2). `PROJECT_PLAN.md` instructs in our own words not to quote ~200 ms. | `Readme.md` and `docs/REPORT.md` say "~200 ms" in several places. That is Kyutai's published figure, never measured here. |
| **Cascade latency** | ~10–28 s warm (`Readme.md`), broken down in `REPORT.md` §9.3 as 9.8 s / ~15 s / ~21 s | `REPORT.md` §3 and §10.7 still quote "~1.9 s", which was measured on a 0.5B stand-in and contradicts §9.3 in the same document |
| **Acoustic branch figures** | 1500 ms / 100 ms error, onset 1200 ms / 145 ms error, repetition ×2 — `backend/scripts/README.md` and root `Readme.md` agree | `REPORT.md` §9.1 quotes 1480 ms / 80 ms, onset 1240 / 105 ms, ×3. Both are real runs; the fixture is re-synthesized each time, so the scripts assert tolerances rather than exact values. Say that if challenged. |
| **Test counts** | 92 backend, 42 frontend (`FIX_LOG.md` verification run, 2026-09-07) | `Readme.md` says 80 backend, 41 frontend |
| **Moshi weight variant** | q8 — no q4 config ships upstream (`M1_BRINGUP_LOG.md`) | `Readme.md` says "Moshi 7B q4" |
| **Retrieval gate** | 0.65 (`config.py`, `calibrate_gate.py` section of `backend/scripts/README.md`) | The `verify_retrieval.py` section of `backend/scripts/README.md` still says "the gate now sits at 0.55" |
| **Whisper model in prose** | `base`, ~600 ms p50 | `services/stt.py`'s module docstring described `small` at "150–300 ms" |
| **The demo script** | `docs/DEMO.md` now has the presenter say the measured figures out loud | it said "~200 ms" and "about 1.9 seconds" |
| **REPORT §11.1** | "the live path is measured but not yet compared" — M12 is what is outstanding | "the live path is unproven", which understated M1 and M2 |
| **Acoustic tag schema** | `backend/app/schemas/acoustic.py` is the binding contract | `docs/ACOUSTIC_TAG_SCHEMA.md` is superseded and says so at the top — unchanged, it already declares this |

`docs/PROJECT_PLAN.md` was deliberately left alone. Its structure is an original
claim followed by a dated correction, and that is the record of how the error
was found — editing it would erase the evidence.

### Machine-dependent, not repository defects

The analyzer and the checkpoint differ by machine, and the deck is careful
about this. On **Track A's box**: `analyzer: heuristic`, `llm_variant: base` —
the trained wav2vec2 checkpoint and the fine-tuned GGUF are gitignored. On
**Track M's box**, with `SCC_DYSFLUENCY_MODEL_PATH` and
`SCC_LLM_VARIANT=finetuned` set in `backend/.env`, the same build reports
`wav2vec2-sep28k` and `finetuned` with no code change.

**Decide before you present which machine is driving the demo, and make sure
the System panel on screen matches what Presenter 2 says on slide 17.** If the
demo runs on Track A's box, say plainly that the trained classifier is present
but not on this machine.

### Two honest findings that are not gaps

- **Retrieval currently degrades reply quality.** The corpus is Edwardian —
  every source predates 1930 — and the same model on the same question gave a
  cleaner answer with retrieval *off* than on. This is a corpus problem, not a
  code problem, and replacing the corpus is the first item of future work.
  Slide 25's speaker notes carry this; raise it yourself rather than being
  caught by it.
- **3B is small.** An A/B on the identical prompt with retrieval off showed an
  8B staying tighter and avoiding the 3B's advice to "pause for about three
  seconds", which is far too long to recommend. It is a hardware trade, taken
  knowingly.

---

## 7 · Before you present

- [ ] Open the deck in PowerPoint and check the substituted fonts render as
      expected on the presentation machine.
- [ ] Decide which machine drives the live demo, and confirm the System panel
      matches slide 17's script.
- [ ] Start the coaching model ~20 s before, Moshi ~60 s before, then the
      backend with models warmed at boot. `docs/DEMO.md` has the pre-flight.
- [ ] Rehearse the slide-13 handoff standing up. It is 45 seconds and it is
      the only place the talk can visibly stumble.
- [ ] Read A14 once through the morning of. The last question on it —
      *what is your actual contribution rather than combining models* — is the
      one that decides the grade.
- [ ] Rehearse slide 21 out loud twice. It carries a safety regression in your
      own fine-tune, and delivering it plainly is worth more than any other
      ninety seconds in the talk. Do not soften it, and do not apologise for
      it — a comparison that only confirmed improvement would be far weaker
      evidence that the harness works.
