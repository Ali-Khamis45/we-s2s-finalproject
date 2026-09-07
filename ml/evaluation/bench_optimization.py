"""Optimization measurement harness (task M10).

The plan claims two optimizations: QLoRA fine-tuning (4-bit NF4 base, so a 3B
model trains on a 16GB Colab T4) and Q4_K_M post-training quantization of the
merged result (so it serves on this laptop at all). This measures the second
one end to end, because it is the one the shipped product depends on at
inference time.

What it measures, per checkpoint and per quantization level:

  - on-disk size, and the compression ratio against f16
  - prompt-eval throughput (tok/s) and generation throughput (tok/s)
  - time to first token (TTFT) over a realistic coaching prompt
  - peak resident memory of the serving process

TTFT and peak RSS are measured against a *served* model over the same HTTP
path the product uses, not in a library harness, because that is the number a
user actually waits for. Throughput comes from `llama-bench`, which controls
for warmup and repetition properly.

Quality delta lives in M9 (`run_eval.py`) rather than here: quantization
quality is a behavioural question, and M9 already runs 11 behavioural checks
over a held-out set. This file measures cost; that file measures whether the
cost bought a regression.

Run it with the **backend** venv, not ml's: the TTFT measurement needs `httpx`,
which lives there (as does `run_eval.py`'s dependency on the same client).

    backend/.venv/Scripts/python.exe ml/evaluation/bench_optimization.py --reps 3

Writes `results/optimization.json` and `results/optimization.md`.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
LLAMA = REPO / "ml" / "finetuning" / "llama.cpp"
GGUF = REPO / "ml" / "finetuning" / "gguf"

# (label, filename, family, precision) — family groups base vs fine-tuned so the
# report can show the quantization effect within each, and the fine-tune effect
# across them.
VARIANTS = [
    ("base f16", "qwen2.5-3b-base-f16.gguf", "base", "f16"),
    ("base Q4_K_M", "qwen2.5-3b-base-Q4_K_M.gguf", "base", "Q4_K_M"),
    ("fine-tuned f16", "qwen2.5-3b-coaching-f16.gguf", "finetuned", "f16"),
    ("fine-tuned Q4_K_M", "qwen2.5-3b-coaching-Q4_K_M.gguf", "finetuned", "Q4_K_M"),
]

# A realistic coaching turn, not "hello" — TTFT depends on prompt length, and
# quoting a figure from a trivial prompt would understate what a user waits for.
TTFT_PROMPT = (
    "I have a job interview tomorrow morning and I keep getting stuck on my "
    "own name when I introduce myself. I have been practising all week and it "
    "still happens. What should I do tonight?"
)


def run_llama_bench(model: Path, reps: int, threads: int) -> dict:
    """Prompt-eval and generation throughput via llama-bench (JSON output)."""
    cmd = [
        str(LLAMA / "llama-bench.exe"),
        "-m", str(model),
        "-p", "512",          # prompt-eval: 512-token prefill
        "-n", "128",          # generation: 128 new tokens
        "-r", str(reps),
        "-t", str(threads),
        "-o", "json",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    if proc.returncode != 0:
        return {"error": (proc.stderr or proc.stdout)[-400:]}

    # llama-bench prints backend-loading lines before the JSON array.
    text = proc.stdout
    start = text.find("[")
    if start < 0:
        return {"error": "no JSON in llama-bench output"}
    try:
        rows = json.loads(text[start:])
    except json.JSONDecodeError as exc:
        return {"error": f"unparseable llama-bench JSON: {exc}"}

    out: dict = {}
    for row in rows:
        # n_prompt>0 is the prefill test; n_gen>0 is the decode test.
        tps = row.get("avg_ts")
        if row.get("n_prompt"):
            out["prompt_tps"] = tps
        elif row.get("n_gen"):
            out["gen_tps"] = tps
    return out


def measure_ttft(model: Path, port: int, threads: int, reps: int) -> dict:
    """Time to first token over the real HTTP serving path.

    Starts llama-server, streams a completion, and stops the clock at the first
    token-bearing chunk. Also samples peak RSS of the server process, which is
    the memory figure that decides whether this model fits alongside Moshi.
    """
    import httpx

    proc = subprocess.Popen(
        [
            str(LLAMA / "llama-server.exe"),
            "-m", str(model),
            "--host", "127.0.0.1", "--port", str(port),
            "-c", "4096", "-t", str(threads),
        ],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        base = f"http://127.0.0.1:{port}"
        deadline = time.time() + 600
        while time.time() < deadline:
            try:
                if httpx.get(f"{base}/health", timeout=2.0).status_code == 200:
                    break
            except Exception:
                pass
            time.sleep(2)
        else:
            return {"error": "server did not become healthy"}

        samples: list[float] = []
        for _ in range(reps):
            started = time.perf_counter()
            first: float | None = None
            with httpx.stream(
                "POST",
                f"{base}/v1/chat/completions",
                json={
                    "messages": [{"role": "user", "content": TTFT_PROMPT}],
                    "max_tokens": 64,
                    "temperature": 0.6,
                    "stream": True,
                },
                timeout=600.0,
            ) as resp:
                for line in resp.iter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    payload = line[6:].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        delta = json.loads(payload)["choices"][0].get("delta", {})
                    except Exception:
                        continue
                    if delta.get("content"):
                        first = (time.perf_counter() - started) * 1000
                        break
            if first is not None:
                samples.append(first)

        peak_mb = sample_peak_rss(proc.pid)
        return {
            "ttft_ms_median": round(statistics.median(samples), 1) if samples else None,
            "ttft_samples": [round(s, 1) for s in samples],
            "peak_rss_mb": peak_mb,
        }
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()


def sample_peak_rss(pid: int) -> float | None:
    """Peak working set of a process, in MB, via tasklist (no extra deps)."""
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=30,
        ).stdout
        m = re.search(r'"([\d,]+) K"', out)
        return round(int(m.group(1).replace(",", "")) / 1024, 1) if m else None
    except Exception:
        return None


def write_report(rows: list[dict], args, path: Path) -> None:
    by_label = {r["label"]: r for r in rows}

    def fmt(v, unit="", nd=0):
        if v is None:
            return "—"
        return f"{v:,.{nd}f}{unit}"

    lines = [
        "# M10 — Optimization Analysis",
        "",
        "*Generated by `ml/evaluation/bench_optimization.py`. "
        f"llama-bench repetitions: {args.reps}, threads: {args.threads}. "
        "CPU inference — the GPU is reserved for Moshi per the plan's architecture.*",
        "",
        "Two optimizations are claimed by this project:",
        "",
        "1. **QLoRA** (M7) — a 4-bit NF4 base with LoRA adapters, which is what",
        "   made fine-tuning a 3B model possible on a free Colab T4 at all.",
        "2. **Q4_K_M post-training quantization** (M8) — what makes the merged",
        "   model servable on an 8GB laptop alongside everything else.",
        "",
        "This file measures (2) directly, since it governs every inference the",
        "shipped product performs. Quality impact is measured separately by M9",
        "(`run_eval.py`), which runs 11 behavioural checks over a held-out set —",
        "cost is measured here, regression is measured there.",
        "",
        "## Size and throughput",
        "",
        "| Variant | Size | vs f16 | Prompt eval | Generation | TTFT (median) | Peak RSS |",
        "|---|---|---|---|---|---|---|",
    ]

    for row in rows:
        ratio = ""
        if row["precision"] == "Q4_K_M":
            f16 = by_label.get(row["label"].replace("Q4_K_M", "f16"))
            if f16 and f16.get("size_mb") and row.get("size_mb"):
                ratio = f"{f16['size_mb'] / row['size_mb']:.2f}x smaller"
        elif row["precision"] == "f16":
            ratio = "—"
        lines.append(
            f"| {row['label']} "
            f"| {fmt(row.get('size_mb'), ' MB')} "
            f"| {ratio or '—'} "
            f"| {fmt(row.get('prompt_tps'), ' tok/s', 1)} "
            f"| {fmt(row.get('gen_tps'), ' tok/s', 1)} "
            f"| {fmt(row.get('ttft_ms_median'), ' ms')} "
            f"| {fmt(row.get('peak_rss_mb'), ' MB')} |"
        )

    lines += [
        "",
        "## Reading this table",
        "",
        "- **Size / vs f16** — the headline compression figure. This is the",
        "  difference between a model that fits in RAM beside Whisper, Kokoro,",
        "  ChromaDB and the dysfluency classifier, and one that does not.",
        "- **Prompt eval vs generation** — prefill is compute-bound and decode is",
        "  memory-bandwidth-bound, so quantization helps them differently. Quoting",
        "  a single 'speed' number would hide that.",
        "- **TTFT** — measured over the real streaming HTTP path with a realistic",
        "  coaching prompt, because it is what a user actually waits for.",
        "- **Peak RSS** — the constraint that decides what can run concurrently.",
        "",
        "## Caveats",
        "",
        "- CPU-only, matching the deployed architecture (GPU reserved for Moshi).",
        "  `ml/finetuning/llama.cpp/README.md` records a measured GPU alternative",
        "  and the CUDA-version trap found while establishing it.",
        "- Throughput is from `llama-bench` with warmup; TTFT is from a live",
        "  server. They are not directly comparable to each other, only across",
        "  rows within each column.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=3, help="llama-bench repetitions")
    ap.add_argument("--ttft-reps", type=int, default=3)
    ap.add_argument("--threads", type=int, default=8)
    ap.add_argument("--port", type=int, default=8090, help="scratch port for TTFT")
    ap.add_argument("--out-dir", default=str(HERE / "results"))
    ap.add_argument("--skip-ttft", action="store_true")
    ap.add_argument(
        "--precisions", default="f16,Q4_K_M",
        help="Comma-separated precisions to bench. Benching f16 on CPU is very "
             "slow (5.9GB, memory-bandwidth-bound) and its throughput is not "
             "what ships; pass 'Q4_K_M' alone when only the served variant "
             "matters. Size and compression ratio are read from the file "
             "regardless, so the f16 column survives either way.",
    )
    args = ap.parse_args()

    wanted = {p.strip() for p in args.precisions.split(",") if p.strip()}

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict] = []
    for label, filename, family, precision in VARIANTS:
        model = GGUF / filename
        if not model.exists():
            print(f"  SKIP {label}: {filename} not found")
            continue

        row = {
            "label": label,
            "family": family,
            "precision": precision,
            "file": filename,
            "size_mb": round(model.stat().st_size / 1048576, 1),
        }
        print(f"\n{label} ({row['size_mb']:,.0f} MB)")

        if precision not in wanted:
            # Size still counts — the compression ratio is the headline figure
            # and comes from the file, not from a benchmark.
            row["skipped"] = "not in --precisions"
            print("  (size only — throughput not benched)")
            rows.append(row)
            continue

        print("  llama-bench…", flush=True)
        row.update(run_llama_bench(model, args.reps, args.threads))
        if "error" in row:
            print(f"    error: {row['error']}")
        else:
            print(f"    prompt {row.get('prompt_tps')} tok/s, "
                  f"gen {row.get('gen_tps')} tok/s")

        if not args.skip_ttft:
            print("  TTFT (live server)…", flush=True)
            row.update(measure_ttft(model, args.port, args.threads, args.ttft_reps))
            print(f"    ttft {row.get('ttft_ms_median')} ms, "
                  f"peak rss {row.get('peak_rss_mb')} MB")

        rows.append(row)

    (out_dir / "optimization.json").write_text(
        json.dumps(rows, indent=2), encoding="utf-8"
    )
    write_report(rows, args, out_dir / "optimization.md")
    print(f"\nwrote {out_dir / 'optimization.md'}")
    print(f"wrote {out_dir / 'optimization.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
