# M8 — merge, GGUF conversion, local serving

Turns M7's LoRA adapter into a locally-servable quantized model.

## Pipeline

1. **Merge** (`merge_adapter.py`): loads `Qwen/Qwen2.5-3B-Instruct` in bf16,
   applies the adapter from `ml/finetuning/adapters/qwen2.5-3b-coaching-lora/`,
   merges into a standalone checkpoint at
   `ml/finetuning/merged/qwen2.5-3b-coaching-merged/` (gitignored, ~6GB).

2. **GGUF conversion**: needs `convert_hf_to_gguf.py`, which is not part of
   llama.cpp's prebuilt binary release — fetch the matching source tag:
   ```
   git clone --depth 1 --branch b10821 https://github.com/ggml-org/llama.cpp.git llama.cpp-src
   pip install sentencepiece gguf protobuf
   python llama.cpp-src/convert_hf_to_gguf.py merged/qwen2.5-3b-coaching-merged --outfile gguf/qwen2.5-3b-coaching-f16.gguf --outtype f16
   ```

3. **Quantize** to Q4_K_M (per the project plan) using the prebuilt
   `llama-quantize.exe`:
   ```
   ./llama.cpp/llama-quantize.exe gguf/qwen2.5-3b-coaching-f16.gguf gguf/qwen2.5-3b-coaching-Q4_K_M.gguf Q4_K_M
   ```
   Result: 5886MB (f16) -> 1835MB (Q4_K_M), ~3.2x compression.

4. **Serve** locally with `llama-server.exe`, matching the OpenAI-compatible
   endpoint `backend/app/services/llm.py` expects:
   ```
   ./llama.cpp/llama-server.exe -m gguf/qwen2.5-3b-coaching-Q4_K_M.gguf --host 127.0.0.1 --port 8080 -c 2048
   ```

## Verified working (2026-09-06)

Sent a real chat completion request to the served model
(`/v1/chat/completions`) with the same system prompt used during M7's
fine-tune. Response was coherent, on-topic, concrete, and matched the
coaching tone/style from the M6 training pairs -- no clinical language, no
generic platitudes. Throughput: ~22 tokens/sec generation, ~55 tokens/sec
prompt processing, CPU-only (GPU stays free for Moshi, per the plan's
architecture).

## Regenerating

All of `ml/finetuning/{adapters,merged,gguf,llama.cpp-src}/` are gitignored
-- they're derived artifacts, reproducible from `ml/finetuning/adapters/`
(the M7 output you download from Colab) plus the steps above.
