"""M11 feasibility probe — can Moshi's backbone be LoRA-adapted on this GPU?

Answers the two questions that decide whether M11 is possible at all, cheaply
and before any dataset work:

  1. Can the PyTorch (trainable) Moshi checkpoint be loaded on this machine?
  2. With LoRA adapters attached, does a forward+backward pass fit in 8GB?

It deliberately does NOT train. It attaches adapters, runs one forward and one
backward pass on random audio-token input, and reports peak VRAM. If that does
not fit, no amount of dataset work makes M11 possible on this hardware, and
that is worth knowing before building a dataset.

Run with the isolated training venv (NOT ml/.venv -- see docs/M11_MOSHI_LORA.md
for the dependency trap this avoids):

    ml/moshi/.venv-train/Scripts/python.exe ml/moshi/probe_lora_feasibility.py

Writes nothing. Prints a verdict.
"""

from __future__ import annotations

import argparse
import sys
import traceback


def gb(n: int | float) -> str:
    return f"{n / 1024**3:.2f} GB"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rank", type=int, default=16, help="LoRA rank (M7 used 16)")
    ap.add_argument("--seq", type=int, default=64, help="sequence length for the probe")
    args = ap.parse_args()

    import torch

    print(f"torch {torch.__version__}  cuda={torch.cuda.is_available()}")
    if not torch.cuda.is_available():
        print("\nFAIL: no CUDA. This venv has a CPU-only torch — see")
        print("docs/M11_MOSHI_LORA.md for the pip default-index trap.")
        return 1

    dev = torch.device("cuda")
    props = torch.cuda.get_device_properties(0)
    print(f"device: {props.name}  {gb(props.total_memory)} total")
    torch.cuda.reset_peak_memory_stats()

    # ---- 1. load the trainable checkpoint --------------------------------
    print("\n[1/3] loading Moshi (PyTorch, trainable)…", flush=True)
    try:
        from moshi.models import loaders
    except Exception:
        print("FAIL: cannot import moshi.models.loaders")
        traceback.print_exc()
        return 1

    try:
        info = loaders.CheckpointInfo.from_hf_repo(loaders.DEFAULT_REPO)
        lm = info.get_moshi(device=dev, dtype=torch.bfloat16)
    except Exception:
        print("FAIL: could not load the Moshi LM checkpoint.")
        print("This is the blocker docs/M11_MOSHI_LORA.md predicts: the cached")
        print("weights are the quantized Candle artifact, and the PyTorch")
        print("checkpoint is a separate multi-GB download.")
        traceback.print_exc()
        return 1

    after_load = torch.cuda.max_memory_allocated()
    total = sum(p.numel() for p in lm.parameters())
    print(f"  loaded. params {total/1e9:.2f}B, peak so far {gb(after_load)}")

    # ---- 2. attach LoRA ---------------------------------------------------
    print(f"\n[2/3] attaching LoRA (r={args.rank})…", flush=True)
    try:
        from peft import LoraConfig, get_peft_model

        # Target the attention projections by name, whatever they are called in
        # this implementation — printing them keeps the probe useful even when
        # the guess is wrong.
        names = {
            n.split(".")[-1]
            for n, m in lm.named_modules()
            if isinstance(m, torch.nn.Linear)
        }
        print(f"  Linear leaf names present: {sorted(names)[:12]}")
        targets = [n for n in ("in_proj", "out_proj", "q_proj", "k_proj",
                               "v_proj", "o_proj", "linear1", "linear2")
                   if n in names]
        if not targets:
            print("  no expected projection names found; using all Linear leaves")
            targets = sorted(names)[:4]
        print(f"  targeting: {targets}")

        lm = get_peft_model(
            lm, LoraConfig(r=args.rank, lora_alpha=args.rank * 2,
                           target_modules=targets, lora_dropout=0.05)
        )
        trainable = sum(p.numel() for p in lm.parameters() if p.requires_grad)
        print(f"  trainable params: {trainable/1e6:.1f}M "
              f"({100*trainable/total:.3f}% of backbone)")
    except Exception:
        print("FAIL: could not attach LoRA adapters.")
        traceback.print_exc()
        return 1

    # ---- 3. forward + backward -------------------------------------------
    print(f"\n[3/3] forward+backward, seq={args.seq}…", flush=True)
    try:
        n_q = getattr(lm, "num_codebooks", None) or 8
        card = getattr(lm, "card", None) or 2048
        tokens = torch.randint(0, card, (1, n_q, args.seq), device=dev)
        out = lm(tokens)
        logits = out.logits if hasattr(out, "logits") else out
        loss = logits.float().pow(2).mean()
        loss.backward()
        peak = torch.cuda.max_memory_allocated()
        print(f"  OK. peak VRAM {gb(peak)} of {gb(props.total_memory)}")
        headroom = props.total_memory - peak
        print(f"  headroom: {gb(headroom)}")
        print("\nVERDICT: a LoRA training step FITS on this GPU.")
        print("The remaining M11 blocker is the dataset (paired coaching audio")
        print("encoded through Mimi), not the hardware. See docs/M11_MOSHI_LORA.md.")
        return 0
    except torch.cuda.OutOfMemoryError:
        print(f"  OOM at peak {gb(torch.cuda.max_memory_allocated())}")
        print("\nVERDICT: does NOT fit at these settings. Options: smaller rank,")
        print("gradient checkpointing, 4-bit base, or shorter sequences.")
        return 2
    except Exception:
        print("FAIL: forward/backward errored (not OOM) — the call signature")
        print("below is the thing to fix; the memory question is unanswered.")
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
