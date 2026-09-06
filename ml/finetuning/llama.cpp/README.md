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

`llama-quantize.exe` (in this same directory after extraction) is what M8
uses to convert the merged fp16 model to Q4_K_M. `llama-server.exe` is what
serves the OpenAI-compatible endpoint `backend/app/services/llm.py` expects
at `SCC_LLM_BASE_URL` (default `http://127.0.0.1:8080/v1`).
