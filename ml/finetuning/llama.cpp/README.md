# llama.cpp (M8)

Prebuilt CPU binaries for Windows x64, fetched from the official
[ggml-org/llama.cpp releases](https://github.com/ggml-org/llama.cpp/releases)
(build b10821), not built from source. CPU build matches the plan's
architecture: llama.cpp serves the cascade's LLM on CPU so the GPU stays
free for Moshi.

Verified working: `./llama-server.exe --version` prints
`version: 0.4.0-dev (build 10821, commit 971595d66)`.

Not committed to git (binaries, regenerable by re-downloading the same
release). To reproduce:

```
curl -sL -o llama-cpu.zip https://github.com/ggml-org/llama.cpp/releases/download/b10821/llama-b10821-bin-win-cpu-x64.zip
unzip -o llama-cpu.zip
```

## GPU serving (measured 2026-09-07)

The CPU build above is the plan's architecture -- the GPU stays free for
Moshi. When Moshi is *not* running, serving the cascade LLM on the GPU is
worth 43x:

| build | prompt eval | generation | coach turn |
|---|---|---|---|
| CPU (b10821) | 56 tok/s | 14 tok/s | ~57 s |
| CUDA 12.4 (b10830) | 31 tok/s | 14 tok/s | ~61 s |
| **CUDA 13.3 (b10830)** | **2423 tok/s** | **88 tok/s** | **~1.4 s** |

**The CUDA version matters, and 12.4 is a trap.** This box has an RTX 5050
(Blackwell, sm_120). The CUDA 12.4 build predates that architecture: it loads
the weights into VRAM -- so `nvidia-smi` shows ~2 GB used and it looks like it
worked -- then falls back to CPU for the compute and runs *slower* than the
CPU build. Only the CUDA 13.3 build carries sm_120 kernels.

Two zips are needed; the runtime is not bundled with the server:

```
curl -sL -o llama-cu133.zip  https://github.com/ggml-org/llama.cpp/releases/download/b10830/llama-b10830-bin-win-cuda-13.3-x64.zip
curl -sL -o cudart-cu133.zip https://github.com/ggml-org/llama.cpp/releases/download/b10830/cudart-llama-bin-win-cuda-13.3-x64.zip
unzip -o llama-cu133.zip -d llama-cu133 && unzip -o cudart-cu133.zip -d llama-cu133
llama-cu133/llama-server.exe --model <path>.gguf --alias qwen2.5-3b-coaching \
  --host 127.0.0.1 --port 8080 --ctx-size 4096 --n-gpu-layers 99
```

Verify it is really on the GPU by the *prompt eval rate*, not by VRAM use:
four figures means GPU compute, two means the CPU fallback described above.

Do not commit these binaries either -- same reason as the CPU ones.

`llama-quantize.exe` (in this same directory after extraction) is what M8
uses to convert the merged fp16 model to Q4_K_M. `llama-server.exe` is what
serves the OpenAI-compatible endpoint `backend/app/services/llm.py` expects
at `SCC_LLM_BASE_URL` (default `http://127.0.0.1:8080/v1`).
