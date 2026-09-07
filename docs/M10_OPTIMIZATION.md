# M10 — Optimization analysis

**The required optimization technique, measured.** Two optimizations are
claimed by this project, and both are load-bearing rather than decorative:
without them the fine-tune could not have been trained and the result could not
be served on this hardware.

1. **QLoRA** (M7) — 4-bit NF4 base + LoRA adapters, which made fine-tuning a 3B
   model possible on a free Colab T4 at all.
2. **Q4_K_M post-training quantization** (M8) — what makes the merged model
   servable on an 8 GB laptop beside Whisper, Kokoro, ChromaDB and the
   dysfluency classifier.

Harness: `ml/evaluation/bench_optimization.py`. CPU inference, 8 threads,
`llama-bench` with warmup, TTFT over the real streaming HTTP path.

---

## 1. Q4_K_M quantization — measured

| Variant | Size | vs f16 | Prompt eval | Generation | TTFT (median) | Peak RSS |
|---|---|---|---|---|---|---|
| base f16 | 5892 MB | — | — | — | — | — |
| **base Q4_K_M** | **1840 MB** | **3.20x smaller** | 69.2 tok/s | 21.1 tok/s | 652.6 ms | 3332.5 MB |
| fine-tuned f16 | 5892 MB | — | — | — | — | — |
| **fine-tuned Q4_K_M** | **1840 MB** | **3.20x smaller** | 69.2 tok/s | 20.0 tok/s | 671.5 ms | 3332.2 MB |

f16 rows report size only. Benchmarking a 5.9 GB f16 model on CPU is
memory-bandwidth-bound and extremely slow, and its throughput is not what
ships — the compression ratio, which is the headline figure, is read from the
files themselves and needs no benchmark.

### What the compression bought

**3.20x smaller: 5892 MB → 1840 MB.** That is the difference between a model
that fits alongside the rest of the stack and one that does not. The plan
budgets 8 GB of system memory for the cascade; peak RSS while serving is
**3.3 GB**, which leaves room for Whisper, Kokoro, ChromaDB and the wav2vec2
classifier to be resident simultaneously. At f16 the model alone would consume
most of the budget.

### Fine-tuning is free at inference time

Base and fine-tuned Q4_K_M are **identical in every cost dimension** — same
1840 MB, same ~69 tok/s prefill, ~20 tok/s decode, ~660 ms TTFT, same 3.33 GB
RSS. Differences between the two rows are within run-to-run noise (the base
model measured 68.9 and 69.2 tok/s prefill on two separate runs).

This is the expected result and worth stating explicitly: LoRA adapters were
**merged into the base weights** (M8) rather than applied at runtime, so the
served artifact is architecturally identical to the base model. The fine-tune's
behavioural differences (M9) cost nothing in size, memory, or speed.

---

## 2. QLoRA — what it enabled

QLoRA is measured by what it made possible rather than by inference throughput,
because it is a *training-time* optimization and leaves no trace in the served
artifact:

- Full fine-tuning of `Qwen2.5-3B-Instruct` in fp16 needs far more VRAM than a
  free Colab T4 (16 GB) provides, once optimizer state and activations are
  counted.
- QLoRA loads the base in **4-bit NF4** and trains only low-rank adapters
  (r=16, alpha=32 — verified in the saved `adapter_config.json`), so the
  trainable parameter count is a small fraction of 3B.
- M7 trained 3 epochs on the T4 within those limits.

Without QLoRA there is no fine-tuned model in this project, and therefore no
M9 comparison. It is the enabling optimization even though it is invisible in
the table above.

---

## 3. Quality delta — and why it is not in this table

The plan lists "quality delta vs FP16" under M10. It is deliberately **not**
measured here, because M9 already measures quality properly and duplicating it
worse would be a mistake.

**What M9 establishes:** the served Q4_K_M fine-tune produces coherent,
on-topic, correctly-styled coaching replies across 125 generations — 100%
usable replies, 100% within a spoken length, 100% speakable. Quantization has
not degraded the model into incoherence, which is the failure mode Q4_K_M
risks.

**The honest limitation:** M9 compares *base Q4_K_M vs fine-tuned Q4_K_M*, so
it isolates the effect of fine-tuning at a fixed precision. It does **not**
isolate the effect of quantization at fixed weights — that would need an f16
arm of the same eval. That arm was not run: at ~57 s per generation on CPU for
a 5.9 GB f16 model, 125 generations is roughly two hours per model, and the
CUDA build that would make it fast is not currently on disk.

Stating the gap plainly: **this project has measured what quantization costs
in size, memory and speed, and has established that the quantized model behaves
correctly — but it has not measured a controlled f16-vs-Q4_K_M quality delta.**
Running `run_eval.py` with both arms served at f16 is the experiment that would
close it, and it is a few hours of compute rather than new engineering.

---

## 4. Serving-build sensitivity — the largest effect measured

The single biggest performance factor found in this project is not the
quantization level but **which llama.cpp build serves the model**
(`ml/finetuning/llama.cpp/README.md`):

| Build | Prompt eval | Generation | Coach turn |
|---|---|---|---|
| CPU (b10821) | 56 tok/s | 14 tok/s | ~57 s |
| CUDA 12.4 (b10830) | 31 tok/s | 14 tok/s | ~61 s |
| **CUDA 13.3 (b10830)** | **2423 tok/s** | **88 tok/s** | **~1.4 s** |

A **43x** swing on a build flag, dwarfing the 3.2x from quantization.

> **The CUDA 12.4 trap.** It *looks* like it works — `nvidia-smi` shows ~2 GB
> of VRAM in use. It loads weights to the GPU, then falls back to CPU for
> compute and lands **slower than the CPU build**. This machine is an RTX 5050
> (Blackwell, `sm_120`) and only the 13.3 build ships `sm_120` kernels.
> **Verify GPU serving by the prompt eval rate, never by VRAM occupancy.**

The CPU build remains the documented default because the plan reserves the GPU
for Moshi. The GPU recipe applies when Moshi is not running — see
`docs/M12_COMPARISON.md` for what that contention means for end-to-end latency.

---

## Summary for the report

> Two optimizations were applied and measured. QLoRA (4-bit NF4 base, LoRA
> r=16/alpha=32) made fine-tuning a 3B model feasible on a free Colab T4 —
> without it there is no fine-tuned model and no M9 comparison. Q4_K_M
> post-training quantization compressed the merged model **3.20x**, from 5892 MB
> to 1840 MB, and it serves at ~69 tok/s prefill, ~20 tok/s decode, ~660 ms time
> to first token, with a 3.3 GB peak resident set that leaves room for the rest
> of the CPU stack. Because the adapters were merged rather than applied at
> runtime, the fine-tuned model is identical to the base in every cost dimension
> — the behavioural changes M9 measured are free at inference time. A controlled
> f16-vs-Q4_K_M quality delta was not run and is named as an open gap rather
> than estimated. The largest single performance factor found was not
> quantization at all but the serving build: CUDA 13.3 outperforms the CPU build
> by 43x, while CUDA 12.4 silently falls back to CPU on this Blackwell card and
> runs slower than CPU despite appearing to use the GPU.
