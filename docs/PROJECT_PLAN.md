# Graduation Project Plan — Native S2S Speech Confidence Coach (Accessibility Domain)

## Context

`FinalProject.pdf` asks for a **Generative AI software product**, not a model or a notebook. The product is a **communication-confidence and speaking-fluency coach for people with speech differences** — *accessibility* is explicitly on the brief's allowed-domain list, and the brief bans medical domains, so nothing in this project uses diagnostic or therapeutic language.

**The flagship is native Speech-to-Speech.** Quantized Moshi is the product's identity: full-duplex, ~200 ms, and it perceives dysfluency directly from audio tokens rather than having it erased by a transcriber. That is the thesis argument and the thing the demo opens with.

There is one constraint that cannot be engineered around, and it drives the whole structure below.

**The brief mandates this pipeline verbatim:**

```
Voice/Text → STT → Prompt Engineering → RAG → Fine-Tuned & Optimized LLM → Response → Optional TTS
```

Five of the eleven required features — STT, RAG, prompt engineering, a fine-tuned LLM, and a base-vs-fine-tuned comparison — have **no place to live inside Moshi**. Native S2S removes the text bottleneck by design, which is exactly why it has no prompt surface, no retrieval injection point, and no text LLM to fine-tune. So the cascade is **demoted, not dropped**: it becomes the Grounded Knowledge path, it must be fully built and genuinely integrated, and it doubles as the fallback if Moshi bring-up fails on Blackwell. Marks come from it either way.

---

## Model recommendation

**Option B — quantized Moshi — is the right choice, with three corrections.**

- **`moshi.cpp` does not exist.** The real quantized paths are Kyutai's Rust/Candle implementation with q4/q8 weights (`kyutai/moshiko-candle-q8`) or `moshi_mlx` on Apple silicon. Plan against Candle.
- **Option A is not viable at all.** OuteTTS is a *text-to-speech* model — a Llama backbone emitting WavTokenizer/DAC audio tokens from **text input**. It cannot ingest speech or perceive dysfluency, and there is no "OuteTTS S2S-1B." It cannot do the job the original proposal wanted from it.
- **The `<300 ms` figure applies to Moshi, not to the cascade.** Moshi genuinely reaches ~200 ms. The cascade is far slower — see the measured figures below. Do not promise 300 ms for the text path in the report; that gap *is* the quantitative result.

### Measured cascade latency

The 750 ms – 1.1 s figure originally written here was an estimate, and measurement
disproved it. Warm-path, CPU, 3.6 s utterance (`backend/scripts/bench_latency.py`):

| Stage | p50 |
|---|---|
| Whisper `base` STT | ~600 ms |
| Acoustic analyzer | 3 ms |
| Prompt assembly | <1 ms |
| LLM time-to-first-token (0.5B) | 30 ms cached / ~3.7 s cold |
| **Time to first audio** | **~1.9 s measured on 0.5B** |

Two things inflate the real figure further: the project ships a **3B** model, not the
0.5B used for this measurement, and CPU decode scales roughly with parameter count.
Expect **seconds**, not one second.

This does not weaken the thesis — it widens the gap against Moshi's ~200 ms, which is
precisely what M12 exists to measure. Quote measurements, never the estimate.

**Moshi's real limitations, stated plainly, because they will come up in the defense:**

- It is **English-only**.
- It is **not instruction-followable**. There is no system prompt and no reliable steering — you cannot tell Moshi "be an encouraging fluency coach" the way you would a chat LLM. The coaching persona has to come from **LoRA fine-tuning on Moshi's backbone (M11)**, or the substantive coaching content has to arrive through the cascade while Moshi carries live rapport and turn-taking.
- It **cannot retrieve**. Grounded, factual coaching content has to come from the cascade.

This is precisely why the dual-mode design below is the honest architecture rather than a compromise.

---

## Architecture — dual-mode, S2S-first

**Moshi's Inner Monologue is the integration hook.** Moshi predicts text tokens as a prefix to its audio tokens. That text stream is readable in real time, and it is what lets a native-S2S product satisfy a text-pipeline rubric coherently: it feeds conversation history, triggers RAG retrieval, and signals when to hand off to Knowledge Mode.

```
                        ┌──────────────────┐
                        │   User Audio     │
                        └────────┬─────────┘
                                 │
              ┌──────────────────┴──────────────────┐
              │                                     │
  ═══════════ ▼ ══════════════════      ═══════════ ▼ ═══════════════
   LIVE COACH MODE  (flagship)           GROUNDED KNOWLEDGE MODE
   Native S2S · ~200 ms · GPU            Cascade · ~1 s · CPU
  ═══════════════════════════════       ═══════════════════════════════
   ┌─────────────────────────┐           ┌──────────────────────────┐
   │  Mimi neural codec      │           │  Whisper (faster-whisper)│
   │  audio → audio tokens   │           │  → text                  │
   └───────────┬─────────────┘           └────────────┬─────────────┘
               │                                      │
   ┌───────────▼─────────────┐           ┌────────────▼─────────────┐
   │  Moshi 7B q4 backbone   │           │  Dysfluency analyzer     │
   │  full-duplex            │           │  wav2vec2 + SEP-28k      │
   │  + LoRA coaching adapter│           └────────────┬─────────────┘
   └───────────┬─────────────┘                        │
               │                           ┌──────────▼─────────────┐
       Inner Monologue text ──────────────▶│  Prompt Engineering    │
               │        (retrieval trigger)│  + acoustic tags       │
               │                           └──────────┬─────────────┘
               │                           ┌──────────▼─────────────┐
               │                           │  RAG · LangChain       │
               │                           │  ChromaDB + bge-small  │
               │                           └──────────┬─────────────┘
               │                           ┌──────────▼─────────────┐
               │                           │  Fine-tuned Qwen2.5-3B │
               │                           │  GGUF Q4_K_M (CPU)     │
               │                           └──────────┬─────────────┘
               │                           ┌──────────▼─────────────┐
               │                           │  Kokoro-82M TTS (CPU)  │
               │                           └──────────┬─────────────┘
   ┌───────────▼──────────────────────────────────────▼─────────────┐
   │        Shared session state · conversation history · metrics   │
   └────────────────────────────────────────────────────────────────┘
```

**Mode routing.** Live Coach Mode is the default and holds the conversation. When the Inner Monologue stream indicates a knowledge-seeking turn ("what should I do about…", "why does…"), the orchestrator hands off to Knowledge Mode, which answers with grounded, cited content. Moshi resumes the live thread afterward.

### VRAM budget — 8 GB, both modes resident

Moshi q4 at ~5.5–6 GB will not share the card with a full second stack, so the cascade runs **on CPU**. This is not a downgrade: every cascade component is small, and Knowledge Mode is not latency-critical.

| Component | Placement | VRAM |
|---|---|---|
| **Moshi 7B q4 + Mimi codec** | **GPU (resident)** | **~5.5–6.0 GB** |
| Moshi LoRA coaching adapter | GPU (merged) | ~0 GB |
| Whisper `base` int8 (CTranslate2) | CPU | 0 |
| wav2vec2-base dysfluency head | CPU | 0 |
| Qwen2.5-3B GGUF Q4_K_M (llama.cpp) | CPU, ~8–15 tok/s | 0 |
| bge-small embeddings + ChromaDB | CPU | 0 |
| Kokoro-82M TTS | CPU | 0 |
| **GPU total** | | **~6.0 / 8 GB** |

Two decisions worth defending in the report:

- **CPU-serving the cascade is what makes S2S-first possible on 8 GB.** It keeps Moshi permanently resident, so there is no model swap and no multi-second mode-switch stall.
- **GGUF via llama.cpp, never bitsandbytes.** The RTX 5050 is Blackwell (`sm_120`), needs CUDA 12.8+ and PyTorch ≥ 2.7, and `bitsandbytes` on `sm_120` is the most fragile link in the stack. Train on Colab/Kaggle T4s (`sm_75`, where bitsandbytes is solid), merge, convert to GGUF, serve locally. The problem disappears.

---

## Task split

**You own the Product/App track. Your partner owns the Model/Hardware track.** RAG ingestion and prompt engineering sit on your side — they are CPU-bound and live inside the backend. Anything that trains, quantizes, or benchmarks sits with your partner.

### Track A — Product & Application (**You**)

| ID | Task | Output |
|---|---|---|
| A1 | Repo scaffold: `git init`, monorepo (`frontend/`, `backend/`, `ml/`, `docs/`), `.gitignore`, `requirements.txt`, `package.json`; copy this plan to `docs/PROJECT_PLAN.md` | Organized GitHub repo (Deliverable 2) |
| A2 | FastAPI skeleton: config, CORS, health check, structured logging, error envelope | `backend/app/main.py` |
| A3 | **`WS /ws/live` — full-duplex Moshi transport.** Bidirectional Opus/PCM streaming, barge-in, no push-to-talk. The flagship path; build first. | Native S2S voice interaction |
| A4 | **Inner Monologue consumer** — read Moshi's text stream, persist to history, run intent detection for Knowledge Mode handoff | The dual-mode hinge |
| A5 | Browser audio: AudioWorklet capture + continuous playback, echo cancellation, 24 kHz mono | `frontend/src/audio/` |
| A6 | `POST /api/chat` + `WS /ws/knowledge` — cascade path end-to-end (also the required text interaction) | Text interaction requirement |
| A7 | Whisper integration (`faster-whisper`, int8, CPU) for Knowledge Mode | **STT requirement** |
| A8 | Kokoro TTS with dynamic speed/pacing driven by acoustic tags | TTS (recommended) |
| A9 | RAG ingestion: PDF/text loaders, semantic chunking (~512 tok, 64 overlap), bge-small, ChromaDB persist | Domain knowledge base |
| A10 | Curate the corpus: public-speaking guides, fluency-shaping technique references, accessibility communication guidance. Non-clinical sources only; **log provenance** | Knowledge base + sources appendix |
| A11 | LangChain retrieval chain: MMR retrieval, top-k=4, reranking, citation passthrough | **RAG requirement** |
| A12 | Prompt engineering module: system persona, few-shot coaching exemplars, structured acoustic-tag injection, context-aware turn conditioning. **Version every prompt** | **Prompt engineering requirement** |
| A13 | Conversation history: SQLite + SQLAlchemy, session/turn/metrics tables, unified across both modes | Required feature |
| A14 | Mode orchestrator: routing, handoff, graceful degradation to cascade-only when Moshi is unavailable | End-to-end integration |
| A15 | React UI: live conversation view, real-time duplex indicator, waveform, transcript with citations | **React frontend requirement** |
| A16 | **Dysfluency timeline overlay** — visualizes acoustic tags per utterance. Highest-value demo asset; makes the novelty visible in five seconds. | Demo centerpiece |
| A17 | Progress dashboard: fluency trend across sessions, pace consistency, session summaries | Measurable user value (brief requires this) |
| A18 | Demo script + recorded walkthrough covering all seven points the brief lists | Deliverable 3 |

### Track M — Models, Training & Hardware (**Partner**)

| ID | Task | Output |
|---|---|---|
| M1 | **Day-one critical path: Moshi on the RTX 5050.** Driver, CUDA 12.8+, Candle build for `sm_120`, q4/q8 weights, measured latency. Everything flagship depends on this. Timebox 2 days; escalate immediately on failure. | Working native S2S host |
| M2 | Moshi streaming service: WebSocket server wrapping Candle, exposing audio in/out **plus the Inner Monologue text stream** for A4 | Flagship model service |
| M3 | Acquire SEP-28k: labels are public, **audio must be fetched from source podcasts** — start day one, it is slow. Fallback: FluencyBank or a reduced label set. | Acoustic dataset |
| M4 | Train the dysfluency classifier: wav2vec2-base + multi-label head (block, prolongation, sound-rep, word-rep, interjection). Report per-class F1. | Acoustic analyzer |
| M5 | Define the acoustic-tag schema **jointly with A12** — the Track A ↔ Track M contract. Freeze in week 1. | Shared JSON schema |
| M6 | Build the coaching instruction dataset (~2–5k pairs): synthetic generation grounded in the A10 corpus, then human curation. Document provenance and filtering. | Fine-tuning dataset |
| M7 | QLoRA fine-tune `Qwen2.5-3B-Instruct` on Colab T4 / Kaggle 2×T4. r=16, alpha=32, 4-bit NF4 base. | **Fine-tuned LLM requirement** |
| M8 | Merge adapter → GGUF → Q4_K_M → validate CPU throughput on the 5050 box | Deployable cascade model |
| M9 | **Base vs. fine-tuned comparison** — held-out eval set, ROUGE-L + BERTScore, LLM-as-judge rubric (empathy, actionability, pacing appropriateness), human study (n=10–15, Likert) | **Required comparison** |
| M10 | **Optimization analysis** — QLoRA + 4-bit quantization measured on model size, peak VRAM, tokens/sec, TTFT, quality delta vs FP16 | **Required optimization technique** |
| M11 | **Moshi LoRA coaching adapter** — fine-tune the backbone toward the coaching persona, since Moshi cannot be system-prompted. Stretch goal; cut if week 7 is tight. | Steerable flagship |
| M12 | Comparative evaluation: Moshi vs cascade on latency (p50/p95), dysfluency perception fidelity, and response groundedness | **Thesis headline result** |

### Shared

| ID | Task | Owner |
|---|---|---|
| S1 | Freeze the acoustic-tag schema (M5) before A12 and M4 diverge | Both, week 1 |
| S2 | Project report — you write architecture/integration/frontend/RAG; partner writes dataset/fine-tuning/optimization/evaluation/S2S comparison | Both |
| S3 | README, setup instructions, dependency manifests | Both |
| S4 | Ethics & scope statement: explicitly non-clinical, no diagnosis, accessibility framing | You (report-critical) |

---

## Rubric coverage

All eleven minimum required features, mapped to owner. **Note how many live in the cascade** — this is why it stays first-class.

| Required feature | Tasks | Owner |
|---|---|---|
| Defined problem, audience, objective | S2, S4 | You |
| Text and voice interaction | A3, A5, A6 | You |
| Speech-to-Text integration | A7 | You |
| Domain-specific RAG knowledge base | A9, A10, A11 | You |
| Prompt engineering techniques | A12 | You |
| Fine-tuned open-source LLM | M6, M7, M8 | Partner |
| Base vs. fine-tuned comparison | M9 | Partner |
| ≥1 optimization technique | M10 | Partner |
| Conversation history / context | A13 | You |
| React frontend + FastAPI backend | A2, A15 | You |
| End-to-end integration | A14 | You |

---

## Sequencing

- **Week 1** — A1, A2, **M1 (Moshi bring-up, critical path)**, M3 (start SEP-28k download *now*), S1
- **Week 2** — M2 (Moshi service), A3, A5 (duplex transport + browser audio) → *flagship talking end-to-end*
- **Week 3** — A4 (Inner Monologue), A9, A10, A11 (RAG); M4, M6 in parallel
- **Week 4** — A6, A7, A8 (cascade path); M7, M8 (fine-tune + deploy)
- **Week 5** — A12, A13, A14 (prompting, history, orchestrator)
- **Week 6** — M9, M10, M12 (evaluation + the comparison result)
- **Week 7** — A15, A16, A17 (UI); M11 (Moshi LoRA, stretch)
- **Week 8** — S2, S3, A18 (report, README, demo)

Moshi comes up first because everything flagship depends on M1, and because a Blackwell failure there has to surface in week 1 while the fallback still has runway.

---

## Risks

| Risk | Mitigation |
|---|---|
| **Moshi fails to run on Blackwell `sm_120`** — the single largest risk, and the whole flagship rests on it | M1 is a day-one 2-day timebox. Fallbacks in order: q8 instead of q4 → CPU Candle inference (degraded but demonstrable) → **cascade becomes the primary product**, which is exactly why A14 has a graceful-degradation path and why the cascade is built regardless. |
| **Moshi cannot be steered into a coaching persona** — no system prompt exists | M11 LoRA adapter. If that is cut, Moshi carries live rapport and turn-taking while all substantive coaching content is delivered through Knowledge Mode. Documented as a limitation, not hidden. |
| **Moshi is English-only** | Scope the product to English and state it explicitly in the report's limitations chapter. |
| **SEP-28k audio retrieval is slow or link-rotted** | Start M3 in week 1. Fallback: FluencyBank, or reduce to a 3-class label set on whatever audio resolves. |
| **CPU cascade too slow to be usable** | 3B Q4_K_M runs ~8–15 tok/s on CPU, acceptable for non-realtime grounded answers. If not, drop to a 1.5B model — the fine-tuning and comparison requirements are size-agnostic. |
| **Medical-domain rejection** | S4 ethics statement, accessibility framing throughout, zero diagnostic language. Confirm framing with the supervisor in week 1. |
| **Track A blocked on Track M** | A14 abstracts both model clients. Track A develops the cascade against base `Qwen2.5-3B` from day one and swaps in the fine-tune when M8 lands. |

---

## Verification

1. **Flagship** — open the UI, hold a full-duplex spoken conversation with barge-in. Confirm measured latency is ~200 ms and that Moshi responds to a simulated block or repetition rather than ignoring it.
2. **Inner Monologue** — confirm the text stream persists to conversation history and correctly triggers a Knowledge Mode handoff on a knowledge-seeking turn.
3. **Cascade** — `POST /api/chat` returns a grounded answer with citations; confirm retrieved chunks appear in the logged prompt.
4. **RAG grounding** — ask something answerable only from the corpus and verify the citation; ask something outside it and verify graceful refusal instead of hallucination.
5. **Dysfluency analyzer** — held-out SEP-28k split, per-class F1 reported in the thesis.
6. **Base vs. fine-tuned** — M9's harness over both checkpoints on the same held-out set; both numbers side by side in the report.
7. **Optimization** — size / peak VRAM / tokens-per-sec / TTFT for FP16 vs Q4_K_M on identical prompts.
8. **The headline comparison** — M12: Moshi vs cascade on latency p50/p95 and dysfluency perception fidelity across ≥50 turns.
9. **Degradation** — kill the Moshi service; confirm the product still holds a coached conversation through the cascade.
10. **Integration** — fresh clone, follow the README, reach a working voice conversation. If that fails, Deliverable 2 fails.
