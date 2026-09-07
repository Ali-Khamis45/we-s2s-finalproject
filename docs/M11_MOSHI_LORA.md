# M11 — Moshi LoRA coaching adapter

**Status: deferred, with the blockers established rather than assumed.**

M11 is the plan's one explicit stretch goal ("cut if week 7 is tight"). This
document records what was verified about its feasibility on this hardware, so
the deferral is a documented engineering decision with evidence behind it
rather than an item that quietly went missing.

M9, M10 and M12 are **required** rubric items; M11 is not. They were completed
first for that reason.

---

## Why the adapter is wanted at all

Moshi has no system prompt. `docs/PROJECT_PLAN.md` puts it plainly: you cannot
tell Moshi "be an encouraging fluency coach" the way you would a chat LLM.
There are exactly two ways to get a coaching persona into the flagship path:

1. **LoRA fine-tune the backbone** toward the persona (this task), or
2. Let Moshi carry live rapport and turn-taking while all substantive coaching
   content arrives through the cascade (the plan's stated fallback).

The product currently ships (2). M12's fidelity and groundedness measurements
turn out to make a stronger case for (2) than the plan anticipated — see
"What M12 changed about this decision" below.

---

## Blockers, as verified on this machine

### 1. The weights on disk are the wrong artifact

What is cached locally is `kyutai/moshiko-candle-q8` (8.0 GB) — a **quantized
Candle inference checkpoint** for the Rust server built in M1. It is not a
trainable PyTorch checkpoint:

- q8-quantized weights are for forward passes, not gradient updates.
- The Candle format serves `moshi-backend.exe`; the PyTorch training stack
  (`peft`, `transformers`) cannot consume it.

Fine-tuning needs the bf16 PyTorch release, which is a **fresh multi-GB
download**. M3 already hit a disk-space near-miss on this box, so this is a
real cost rather than a footnote.

### 2. No training pipeline exists, and M7's does not transfer

M7 fine-tuned Qwen with `trl` over 400 **text** pairs — a solved, well-trodden
path. Moshi's backbone consumes **audio tokens**: paired conversational audio
encoded through Mimi into RVQ codebooks, with the depth transformer modelling
the codebook hierarchy.

M6's 400 text pairs cannot be fed to it directly. Producing usable training
data would mean synthesizing or sourcing paired coaching *audio* and encoding
it — a dataset-construction task on the scale of M3+M6 combined, not a
preprocessing step.

`peft` is installed; the `moshi` PyTorch package is not.

### 2a. A dependency trap found while attempting this (2026-09-07)

`pip install moshi` into `ml/.venv` **silently broke GPU support** and had to be
reverted. It is worth recording because anyone retrying this will hit it:

- `moshi` 0.2.13 pins `torch<2.10`. This project runs **torch 2.11.0+cu128**,
  needed for Blackwell/`sm_120`.
- pip resolved that pin by installing **`torch 2.9.1+cpu`** — a CPU-only build.
  `torch.cuda.is_available()` went from `True` to `False`, silently. Nothing
  errored; the GPU simply disappeared from the environment.
- It also downgraded `numpy` 2.5.2 → 2.2.6 and `aiohttp`.

Recovery: force-reinstall `torch==2.11.0` from the `cu128` index, uninstall
`moshi`, then verify — `torch.cuda.is_available()` is `True` again, and M4's
classifier still returns sane predictions. The environment was checked working,
not merely reinstalled.

**The useful finding underneath the trap:** `torch 2.9.1+cu128` *does* exist on
the PyTorch CUDA 12.8 index, and it satisfies moshi's `<2.10` pin. So the
conflict is a pip *default-index* artifact, not a real Blackwell
incompatibility — this blocker is softer than it first appeared. The correct
setup is a **separate venv** (`ml/moshi/.venv-train`) with
`torch==2.9.1+cu128`, so the working `ml/.venv` is never at risk.

**It bites a second time if you install in the wrong order.** Installing
`torch==2.9.1+cu128` first and *then* `moshi` does not work: pip re-resolves
torch while satisfying moshi's dependencies and silently swaps the CUDA build
for the CPU wheel from PyPI. The venv ends up with `2.9.1+cpu` again, and again
nothing errors.

The order that works:

```bash
python -m venv ml/moshi/.venv-train
ml/moshi/.venv-train/Scripts/python.exe -m pip install moshi
# then put the CUDA build back, and stop pip re-resolving it away:
ml/moshi/.venv-train/Scripts/python.exe -m pip install --force-reinstall --no-deps \
    torch==2.9.1 --index-url https://download.pytorch.org/whl/cu128
```

`--no-deps` is the load-bearing flag. **Always verify with
`torch.cuda.is_available()` afterwards** — this failure mode is silent in both
directions, and an install that "succeeded" with exit code 0 tells you nothing.

### 3. 8 GB VRAM is tight for a 7B backbone plus depth transformer

The card is an RTX 5050 Laptop (8151 MiB total, ~1.2 GB already resident).
M1's bring-up needed q8 quantization just to *serve* this model. Training it,
even with 4-bit + LoRA, puts optimizer state and activations on top of that
budget. Not obviously impossible, but not a safe assumption either — and the
sm_120/Blackwell toolchain friction documented in `docs/M1_BRINGUP_LOG.md`
(three sequential toolchain bugs) applies to any new build here.

### 4. There would be no way to evaluate the result

This is the blocker that matters most, and it is the least obvious.

M9 can score a text model because replies are text: 11 deterministic
behavioural checks over a held-out set. A Moshi adapter produces **speech**.
There is no equivalent harness, and M12's fidelity work established why one is
hard: Moshi exposes no intermediate representation to score. "The persona
sounds better" is not a measurement, and shipping an unevaluated fine-tune
into a product in an accessibility domain is worse than shipping none.

Building that evaluation capability is itself a project-sized task.

---

## What M12 changed about this decision

M12 measured two things that bear directly on whether M11 is worth its cost:

- **Dysfluency perception fidelity** — the cascade scores a macro F1 of 0.612
  against human SEP-28k labels on held-out clips. Moshi scores nothing,
  because it has no dysfluency representation to score at all.
- **Groundedness** — the cascade grounds 100% of in-corpus questions and 0% of
  out-of-corpus ones, correctly refusing both the medication and the diagnosis
  question. Moshi has no corpus and cannot cite.

A LoRA adapter would change Moshi's *persona*. It would not give it a
perception stage, a corpus, or the ability to cite — the three capabilities the
coaching content actually depends on. So even a fully successful M11 leaves
the dual-mode architecture intact and the substantive content flowing through
the cascade.

That materially lowers M11's value: it is a polish item on the flagship's
delivery, not a capability unlock.

---

## What it would take, concretely

If picked up later, in order:

1. Download the bf16 PyTorch Moshi release; confirm free disk first.
2. Install the `moshi` PyTorch package and confirm a forward pass on this GPU.
3. Build a paired coaching-audio dataset and encode it through Mimi to RVQ
   codebooks. **This is the long pole** — plan it as its own task with its own
   quality gate, the way M6 had one.
4. LoRA-adapt the backbone (temporal transformer first; the depth transformer
   is smaller and may not need adapting).
5. Build a speech-output evaluation harness before training anything, so the
   result can be judged. Without step 5, steps 1–4 produce an unfalsifiable
   claim.

Estimated at 2–4 focused sessions with a genuine risk of ending with no
trainable artifact — which is why it ran after the required work, not before.

---

## Honest statement for the report

> M11 was scoped as a stretch goal and deferred. Moshi cannot be
> system-prompted, so its coaching persona would require LoRA fine-tuning of
> the backbone. Four blockers were established rather than assumed: the cached
> weights are a quantized Candle inference artifact rather than a trainable
> PyTorch checkpoint; no speech-token training pipeline or paired coaching-audio
> dataset exists, and M6's text pairs do not transfer; 8 GB of VRAM is tight for
> training a 7B backbone; and no harness exists to evaluate a speech-output
> fine-tune, making the result unfalsifiable. M12's measurements further showed
> that an adapter would alter persona without supplying the perception stage,
> corpus, or citations that the coaching content depends on — so the dual-mode
> architecture, in which the cascade carries substance and Moshi carries
> rapport, stands regardless. The limitation is documented rather than hidden.
