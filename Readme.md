# Speech Confidence Coach

A native speech-to-speech AI coach that helps people with speech differences build
communication confidence — practising pacing, fluency techniques, and delivery in a
low-pressure spoken conversation.

**Scope note:** this is an *accessibility* and communication-coaching tool. It is not a
medical device, it does not diagnose, and it does not provide clinical speech therapy.
See [`docs/ETHICS.md`](docs/ETHICS.md).

---

## Why native speech-to-speech

A conventional voice assistant transcribes speech to text before reasoning about it. That
step destroys the signal that matters most here: `"I-i-i want... water"` becomes
`"I want water"`, and every trace of the repetition, the block, and its duration is gone
before the model ever sees it.

This project's flagship path is **native S2S** — quantized Moshi — which consumes audio
tokens directly and can perceive dysfluency as an acoustic event rather than a
transcription artifact. It runs full-duplex at roughly 200 ms, so the coach can respond
inside the rhythm of a real conversation instead of after it.

A second **Grounded Knowledge** path (Whisper → dysfluency analyzer → prompt engineering →
RAG → fine-tuned LLM → TTS) handles turns that need retrieved, cited, factual coaching
content, which native S2S structurally cannot provide. Comparing the two is the project's
central research result.

Full architecture, task split, and rationale: [`docs/PROJECT_PLAN.md`](docs/PROJECT_PLAN.md).

---

## Architecture at a glance

| Mode | Path | Latency | Runs on |
|---|---|---|---|
| **Live Coach** (flagship) | Mimi codec → Moshi 7B q4 → audio out | ~200 ms | GPU (~6 GB) |
| **Grounded Knowledge** | Whisper → wav2vec2 tags → prompt → RAG → Qwen2.5-3B → Kokoro | ~1 s | CPU |

Moshi's Inner Monologue text stream is what bridges them: it feeds conversation history
and triggers the handoff to Knowledge Mode on knowledge-seeking turns.

---

## Repository layout

```
backend/          FastAPI service — WebSocket transports, RAG, orchestration
  app/api/          route handlers
  app/core/         config, logging
  app/services/     STT, TTS, retrieval, Moshi client
  app/models/       SQLAlchemy models
frontend/         React + Vite UI
  src/audio/        AudioWorklet capture and duplex playback
  src/components/   conversation view, dysfluency timeline, dashboard
ml/               Track M — training and evaluation (runs on Colab/Kaggle, not locally)
  dysfluency/       wav2vec2 + SEP-28k classifier
  finetuning/       QLoRA on Qwen2.5-3B-Instruct
  moshi/            Moshi serving and LoRA adapter
  evaluation/       base-vs-fine-tuned, optimization, S2S-vs-cascade benchmarks
data/             corpus, vector store (contents gitignored)
docs/             project plan, report, ethics statement
```

---

## Setup

Requires Python 3.11+ and Node 20+. The flagship path additionally needs an NVIDIA GPU
with at least 8 GB VRAM; everything else runs on CPU.

**The app starts with no models downloaded.** Services load lazily, so you can run the
server, open the UI, and see exactly what is missing at `GET /api/status` before
installing anything heavy. That is deliberate — a server that refuses to boot without
weights is a server nobody can debug.

### Backend

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate          # Windows;  source .venv/bin/activate elsewhere
pip install -r requirements.txt
cp .env.example .env            # optional; every setting has a default
uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000/docs for the full API, or `/api/status` for a one-line
readout of what is currently available.

### Frontend

```bash
cd frontend
npm install
npm run dev                     # http://localhost:5173
```

Vite proxies `/api`, `/health`, and `/ws` to the backend, so both run on one origin.

### Bringing the pieces online

Each is independent — the app degrades cleanly with any of them missing.

| Piece | How | Without it |
|---|---|---|
| **Coaching model** | `llama-server -m qwen2.5-3b-instruct-q4_k_m.gguf --port 8080 -c 4096` | Chat returns a clear 503 |
| **STT + TTS** | Downloaded automatically on first use | Voice turns fail; typing works |
| **Knowledge base** | Put documents in `data/corpus`, then `POST /api/corpus/ingest` | Answers are ungrounded and say so |
| **Live coach** | Track M's Moshi service on `ws://127.0.0.1:8998` (task M2) | Falls back to Knowledge Mode automatically |

### Tests

```bash
cd backend && .venv\Scripts\python -m pytest    # 35 tests
cd frontend && npm test                          # 30 tests
```

The backend suite runs without any model present, which is the point: it covers routing,
persistence, the acoustic contract, and — most importantly — that the product still
works when the flagship is down. The frontend suite covers the timeline geometry, the
gapless audio scheduling, and the API error envelope.

Beyond the unit tests, [`backend/scripts/`](backend/scripts/) holds verification scripts
that run the real models against known inputs and reproduce every measurement quoted in
the report.

### Training environment (Track M)

`ml/requirements.txt` is for **Colab T4 or Kaggle 2×T4 only**. Do not install it on the
RTX 5050: the card is Blackwell (`sm_120`), and `bitsandbytes` support there is the most
fragile dependency in the stack. Train in the cloud, merge the adapter, convert to GGUF,
and serve locally with llama.cpp — which has solid Blackwell support and avoids the
problem entirely.

---

## Status

The application layer (Track A) is built and tested: both WebSocket paths, the cascade,
RAG, prompt engineering, conversation history, and the React UI including the dysfluency
timeline and progress dashboard. The full Knowledge Mode path has been run end to end
against a served model — see [`backend/scripts/`](backend/scripts/), whose output
reproduces every measurement quoted below and in the report.

Two figures worth knowing before reading further, both measured rather than estimated:

- Against a **1400 ms** block spliced into real speech, the analyzer recovered
  **1480 ms**, located within 105 ms. Whisper's transcript contained no trace of it.
- Time to first audio on the cascade is **~1.9 s**, not the ~1 s originally planned.
  Whisper is the dominant cost. The live path's ~200 ms is unchanged, so the gap the
  thesis measures is wider than expected, not narrower.

The project report is in [`docs/REPORT.md`](docs/REPORT.md) and the demo walkthrough in
[`docs/DEMO.md`](docs/DEMO.md). Sections of the report owned by the model track are
marked `[TRACK M]` with the command that produces each set of numbers.

Two things are stubs waiting on Track M. The **live coach** needs the Moshi service
(M2) — the client is written and the app falls back automatically until it exists. The
**dysfluency analyzer** currently runs a heuristic backend built from Whisper word
timings and frame energy; it is a development scaffold so the UI and prompt contract
could be built without waiting for SEP-28k, and it is **not citable in the thesis**.
`GET /api/status` reports which backend is live, and the trained classifier (M4) drops
in behind the same interface via `SCC_DYSFLUENCY_MODEL_PATH`.

The **knowledge base is populated** with a starter corpus: five public-domain books on
public speaking and vocal delivery, 313,866 words across 1,057 chunks. Rebuild it with
`python backend/scripts/fetch_corpus.py`, and see
[`data/corpus/SOURCES.md`](data/corpus/SOURCES.md) for full provenance, the one book
excluded on ethical review, and the limitations to state in the report — these texts are
a century old and should be supplemented with modern licensed material before
submission.

See [`docs/PROJECT_PLAN.md`](docs/PROJECT_PLAN.md) for the full task breakdown and the
week-by-week sequence.
