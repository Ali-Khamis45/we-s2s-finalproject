# Demo runbook — Knowledge Mode on GPU

**Read this first, then run the three commands.** This is the exact
configuration verified working on 2026-09-07: first audio **7.1 s**, full
coaching turn **1.4 s**, LLM at **94 tok/s**.

Moshi / Live Coach is **deliberately off**. See "Why no Moshi" at the bottom —
it is a hardware limit worth stating out loud in the presentation, not a bug to
apologise for.

---

## Before anything: check `backend/.env`

These five lines must be present. They are gitignored, so a fresh clone will
not have them.

```
SCC_DYSFLUENCY_MODEL_PATH=../ml/dysfluency/checkpoints/wav2vec2-dysfluency
SCC_LLM_BASE_URL=http://127.0.0.1:8080/v1
SCC_LLM_VARIANT=finetuned
SCC_PROMPT_COMPACT=1
SCC_MOSHI_ENABLED=false
```

`SCC_MOSHI_ENABLED=false` is what stops the UI offering a Live Coach that
cannot run. Without it the app tries, fails, and shows "the live coach dropped
out" in front of the audience.

---

## Start (three terminals, in this order)

Run each from the repository root:
`C:\Courses\WE Advanced AI\Final Project\we-s2s-finalproject`

### 1. LLM on GPU

```bash
cd ml/finetuning
./llama-cu133/llama-server.exe -m gguf/qwen2.5-3b-coaching-Q4_K_M.gguf \
  --alias qwen2.5-3b-coaching --host 127.0.0.1 --port 8080 \
  -c 4096 --n-gpu-layers 99
```

**It must be `llama-cu133`, not `llama.cpp`.** The `llama.cpp` directory holds
the CPU build, which serves the same model roughly 43x slower (~57 s per turn
instead of ~1.4 s). Both exist; only one is fast.

Wait for the server to report ready before starting the backend.

### 2. Backend

```bash
cd backend
./.venv/Scripts/python.exe -m uvicorn app.main:app --port 8000 --host 127.0.0.1
```

### 3. Frontend

```bash
cd frontend
npm run dev
```

Vite prints the URL. It is normally **http://localhost:5173**, but it silently
picks 5174 if 5173 is taken by a leftover process — read the line it prints
rather than assuming.

---

## Verify before presenting (10 seconds, worth it)

```bash
curl -s http://127.0.0.1:8000/api/status
```

Expect exactly this shape:

```json
{
  "live_available": false,
  "llm_reachable": true,
  "corpus_chunks": 1057,
  "corpus_status": "ok",
  "analyzer": "wav2vec2-sep28k",
  "prompt_version": "a12-v5-compact",
  "llm_variant": "finetuned"
}
```

What each line is telling you:

| Field | Must be | If it is wrong |
|---|---|---|
| `live_available` | `false` | `SCC_MOSHI_ENABLED` is not `false` — the UI will offer a broken mode |
| `llm_reachable` | `true` | the llama server is not up, or is on the wrong port |
| `analyzer` | `wav2vec2-sep28k` | the real M4 checkpoint is missing; it has fallen back to a heuristic |
| `llm_variant` | `finetuned` | it is serving the base model, so the fine-tune is not being demonstrated |

`stt_loaded` may be `false` until the first spoken turn — Whisper loads lazily.
That is normal.

### Confirm the GPU is really being used

VRAM occupancy is **not** proof. A CUDA build that is silently falling back to
CPU still shows GPU memory in use. Check the rate instead — the llama server
prints it after each turn:

```
eval time = ... (94.27 tokens per second)
```

Two figures (~14 tok/s) means it is on CPU and the demo will crawl. Roughly
90+ tok/s means the GPU is doing the work.

---

## During the demo

- **Speak, don't type.** Typed turns run the same pipeline but are answered in
  text only (`speak=False` by design), so no audio plays. Use the mic to show
  the full STT → analysis → RAG → LLM → TTS path.
- **First turn of a session is slower** than the rest — Whisper and Kokoro load
  on first use. If you want a completely smooth opening, do one throwaway turn
  before the audience is watching.
- Expect roughly **7 s to first audio**, then continuous speech.

---

## If something breaks mid-demo

**No audio at all.** You are probably typing rather than speaking. Otherwise
check the llama server terminal is still alive.

**Very slow (tens of seconds per turn).** The CPU build is being served. Stop
it and restart from `llama-cu133`.

**"Live coach dropped out."** `SCC_MOSHI_ENABLED` is not `false`. Set it and
restart the backend.

**Port already in use.** A previous run is still alive:

```bash
taskkill //F //IM llama-server.exe
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"name='python.exe' or name='node.exe'\" | Where-Object {\$_.CommandLine -match 'uvicorn|vite'} | ForEach-Object { Stop-Process -Id \$_.ProcessId -Force }"
```

---

## Shut down

```bash
taskkill //F //IM llama-server.exe
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"name='python.exe' or name='node.exe'\" | Where-Object {\$_.CommandLine -match 'uvicorn|vite'} | ForEach-Object { Stop-Process -Id \$_.ProcessId -Force }"
```

---

## Why no Moshi — say this, don't hide it

Moshi needs **~7.7 GB of an 8.15 GB card**. That leaves ~200-400 MB, which is
not enough for its per-session buffers once a browser and other GPU apps are
resident. On 2026-09-07 that produced three distinct failures in one session: a
CUDA out-of-memory after repeated connect cycles, sessions dying instantly for
want of buffer memory, and a websocket keepalive killing sessions where Moshi
was merely slow.

The measurements make this a finding rather than an excuse:

- Moshi's time to first audio on this hardware is **p50 2009.9 ms** (M12) — not
  the ~200 ms the project originally assumed.
- The cascade on GPU answers a full turn in **~1.4 s**.

**So the cascade is faster than the flagship on this hardware, and the two
modes cannot share the card.** That is precisely the architectural constraint
M12 exists to document — see `docs/M12_COMPARISON.md`.

Everything the brief requires is still demonstrated: STT, RAG with citations,
prompt engineering, the fine-tuned LLM, dysfluency detection on the real
SEP-28k-trained checkpoint, TTS with acoustic-driven pacing, conversation
history, and the React UI.

To bring Moshi back afterwards: stop the GPU llama server (it holds 2.5 GB),
set `SCC_MOSHI_ENABLED=true`, start `moshi-backend.exe` and `ml/moshi/bridge.py`,
and restart the backend.

---

## Notes for a fresh machine

Two things this setup depends on are **gitignored** and will not come from a
clone:

- `ml/finetuning/llama-cu133/` — the CUDA 13.3 build. Rebuild with:
  ```bash
  curl -sL -o llama-cu133.zip  https://github.com/ggml-org/llama.cpp/releases/download/b10830/llama-b10830-bin-win-cuda-13.3-x64.zip
  curl -sL -o cudart-cu133.zip https://github.com/ggml-org/llama.cpp/releases/download/b10830/cudart-llama-bin-win-cuda-13.3-x64.zip
  unzip -o llama-cu133.zip -d llama-cu133 && unzip -o cudart-cu133.zip -d llama-cu133
  ```
  Use **13.3**, not 12.4: the 12.4 build predates this card's `sm_120`
  architecture and silently falls back to CPU while appearing to use the GPU.
- `ml/finetuning/gguf/qwen2.5-3b-coaching-Q4_K_M.gguf` — regenerate per
  `ml/finetuning/M8_README.md`.
