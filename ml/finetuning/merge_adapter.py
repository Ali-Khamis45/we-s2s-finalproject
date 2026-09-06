"""M8: merge the M7 LoRA adapter into the base Qwen2.5-3B-Instruct model.

Loads the base model in bf16 (not 4-bit -- merging needs full-precision
weights to combine with the LoRA deltas correctly), applies the adapter,
merges it into the base weights, and saves a standalone fp16/bf16 model
ready for GGUF conversion (llama-quantize needs a plain HF checkpoint, not
a PEFT adapter directory).

Usage:
    python merge_adapter.py
    python merge_adapter.py --device cpu   # if the GPU merge OOMs
"""
from __future__ import annotations

import argparse
from pathlib import Path

import torch
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer

REPO_ROOT = Path(__file__).resolve().parents[2]
BASE_MODEL = "Qwen/Qwen2.5-3B-Instruct"
DEFAULT_ADAPTER_DIR = REPO_ROOT / "ml" / "finetuning" / "adapters" / "qwen2.5-3b-coaching-lora"
DEFAULT_OUT_DIR = REPO_ROOT / "ml" / "finetuning" / "merged" / "qwen2.5-3b-coaching-merged"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adapter-dir", type=Path, default=DEFAULT_ADAPTER_DIR)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    args = parser.parse_args()

    print(f"Loading base model {BASE_MODEL} on {args.device}...")
    base_model = AutoModelForCausalLM.from_pretrained(
        BASE_MODEL,
        torch_dtype=torch.bfloat16,
        device_map=args.device,
    )
    tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL)

    print(f"Loading adapter from {args.adapter_dir}...")
    model = PeftModel.from_pretrained(base_model, args.adapter_dir)

    print("Merging adapter into base weights...")
    merged = model.merge_and_unload()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    print(f"Saving merged model to {args.out_dir}...")
    merged.save_pretrained(args.out_dir, safe_serialization=True)
    tokenizer.save_pretrained(args.out_dir)

    print(f"Done. Merged model at {args.out_dir}")


if __name__ == "__main__":
    main()
