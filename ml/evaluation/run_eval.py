"""Base vs. fine-tuned comparison harness (task M9).

Runs the held-out eval set through two checkpoints over the EXACT production
prompt — it imports `app.services.prompts.templates` rather than reimplementing
it, because a comparison run on a different prompt than the product uses proves
nothing about the product.

Both checkpoints see identical prompts, identical acoustic context, and
identical decoding settings. The only variable is the weights.

    python ml/evaluation/run_eval.py \
        --base-url    http://127.0.0.1:8080/v1 --base-model qwen-base \
        --tuned-url   http://127.0.0.1:8081/v1 --tuned-model qwen-tuned

Serve the two checkpoints on different ports (llama-server twice), or pass the
same URL with different --*-model names if one server holds both.

Outputs `results.json` (every reply, for the appendix) and `report.md` (the
comparison table). Retrieval is deliberately NOT run here: M9 compares model
behaviour, and mixing in retrieval variance would confound it. The knowledge and
out_of_corpus cases still exercise whether the model invents an answer when it
has been given no material, which is the behaviour worth measuring.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "backend"))

from checks import CHECKS, run_checks  # noqa: E402

from app.schemas.acoustic import (  # noqa: E402
    AcousticProfile,
    DysfluencyEvent,
    DysfluencyKind,
    ProsodyMetrics,
)
from app.services.prompts import templates  # noqa: E402


def build_profile(spec: dict | None) -> AcousticProfile | None:
    """Expand the compact eval-set spec into a real AcousticProfile.

    The eval set stores `{"events": [["block", 1400]], "wpm": 82}` because that
    is readable and editable by hand; the prompt builder needs the full object.
    """
    if not spec:
        return None

    events: list[DysfluencyEvent] = []
    cursor = 300
    for kind, duration in spec.get("events", []):
        events.append(
            DysfluencyEvent(
                kind=DysfluencyKind(kind),
                start_ms=cursor,
                end_ms=cursor + int(duration),
                confidence=0.9,
            )
        )
        cursor += int(duration) + 250

    return AcousticProfile(
        duration_ms=int(spec.get("duration_ms", 4000)),
        events=events,
        prosody=ProsodyMetrics(
            speech_rate_wpm=spec.get("wpm"),
            pitch_variation=spec.get("pitch_variation"),
            longest_pause_ms=max((int(d) for _, d in spec.get("events", [])), default=None),
        ),
        analyzed=True,
        source="eval_set",
    )


async def generate(
    client, model: str, messages: list, max_tokens: int, temperature: float
) -> tuple[str, float]:
    body = {
        "model": model,
        "messages": [m.as_dict() for m in messages],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": False,
    }
    started = time.perf_counter()
    resp = await client.post("/chat/completions", json=body)
    resp.raise_for_status()
    ms = (time.perf_counter() - started) * 1000
    data = resp.json()
    return (data["choices"][0]["message"]["content"] or "").strip(), ms


async def run_variant(
    name: str, url: str, model: str, cases: list[dict], args
) -> list[dict]:
    """Generate a reply per case, `--runs` times.

    Sampling matters here. At temperature 0.6 a single pass over 25 cases gives
    categories with n=2 or n=3, where one unlucky generation swings a rate by 33
    points. Averaging over several runs is the difference between a measurement
    and an anecdote — the report should say how many runs produced its numbers.
    """
    import httpx

    rows: list[dict] = []
    async with httpx.AsyncClient(base_url=url.rstrip("/"), timeout=180.0) as client:
        for run in range(1, args.runs + 1):
            for i, case in enumerate(cases, start=1):
                profile = build_profile(case.get("acoustic"))
                bundle = templates.build(
                    user_text=case["user"],
                    acoustic=profile,
                    citations=[],
                    history=[],
                )
                try:
                    reply, ms = await generate(
                        client, model, bundle.messages, args.max_tokens, args.temperature
                    )
                except Exception as exc:
                    print(f"    [{name}] {case['id']}: FAILED — {exc}")
                    reply, ms = "", 0.0

                rows.append(
                    {
                        "id": case["id"],
                        "run": run,
                        "category": case["category"],
                        "user": case["user"],
                        "reply": reply,
                        "latency_ms": round(ms, 1),
                        "checks": run_checks(case, reply),
                        "prompt_version": bundle.version,
                    }
                )
                suffix = f" (run {run}/{args.runs})" if args.runs > 1 else ""
                print(f"    [{name}] {i}/{len(cases)} {case['id']}{suffix}", flush=True)
    return rows


def score(rows: list[dict]) -> dict[str, tuple[int, int]]:
    """(passed, applicable) per check."""
    out: dict[str, tuple[int, int]] = {}
    for check in CHECKS:
        passed = applicable = 0
        for row in rows:
            v = row["checks"].get(check.key)
            if v is None:
                continue
            applicable += 1
            passed += bool(v)
        out[check.key] = (passed, applicable)
    return out


def pct(passed: int, applicable: int) -> str:
    return f"{100 * passed / applicable:.0f}%" if applicable else "—"


def write_report(base_rows, tuned_rows, args, path: Path) -> None:
    base_s, tuned_s = score(base_rows), score(tuned_rows)

    lines: list[str] = [
        "# Base vs. Fine-Tuned Comparison",
        "",
        f"*Generated by `ml/evaluation/run_eval.py`. Prompt version "
        f"`{base_rows[0]['prompt_version'] if base_rows else '?'}`, "
        f"temperature {args.temperature}, max tokens {args.max_tokens}, "
        f"{args.runs} run(s) per case.*",
        "",
        (
            "> **Sample size.** The `n` column counts generations, not cases. At "
            "temperature above zero a single run leaves some categories with only "
            "two or three samples, where one unlucky generation moves a rate by "
            "tens of points. Do not quote a difference smaller than the noise; "
            "raise `--runs` until the numbers stabilise."
            if args.runs < 5 else
            "> Rates are averaged over multiple generations per case."
        ),
        "",
        f"- Base: `{args.base_model}` at `{args.base_url}`",
        f"- Fine-tuned: `{args.tuned_model}` at `{args.tuned_url}`",
        f"- Cases: {len(base_rows)}",
        "",
        "Both checkpoints saw identical prompts, identical acoustic context, and",
        "identical decoding settings. The only variable is the weights.",
        "",
        "## Behavioural checks",
        "",
        "Each maps to a specific rule in the system prompt or `docs/ETHICS.md`.",
        "Higher is better throughout.",
        "",
        "| Check | Base | Fine-tuned | Δ | n |",
        "|---|---|---|---|---|",
    ]

    for check in CHECKS:
        bp, ba = base_s[check.key]
        tp, ta = tuned_s[check.key]
        if ba == 0 and ta == 0:
            continue
        delta = ""
        if ba and ta:
            d = (100 * tp / ta) - (100 * bp / ba)
            delta = f"{d:+.0f} pts" if abs(d) >= 0.5 else "—"
        lines.append(
            f"| {check.label} | {pct(bp, ba)} | {pct(tp, ta)} | {delta} | {max(ba, ta)} |"
        )

    b_lat = [r["latency_ms"] for r in base_rows if r["latency_ms"]]
    t_lat = [r["latency_ms"] for r in tuned_rows if r["latency_ms"]]
    lines += [
        "",
        "## Generation latency",
        "",
        "| | Base | Fine-tuned |",
        "|---|---|---|",
        f"| median | {statistics.median(b_lat):.0f} ms | {statistics.median(t_lat):.0f} ms |"
        if b_lat and t_lat else "| median | — | — |",
        "",
        "## What each check means",
        "",
    ]
    for check in CHECKS:
        lines.append(f"- **{check.label}** — {check.why}")

    lines += [
        "",
        "## Side-by-side replies",
        "",
        "The qualitative half. An examiner will ask what the base model does *wrong*,",
        "and a percentage does not answer that.",
        "",
    ]
    tuned_by_id = {r["id"]: r for r in tuned_rows}
    for row in base_rows:
        other = tuned_by_id.get(row["id"], {})
        failed = [k for k, v in row["checks"].items() if v is False]
        lines += [
            f"### `{row['id']}` — {row['category']}",
            "",
            f"> {row['user']}",
            "",
            f"**Base**{' — failed: ' + ', '.join(failed) if failed else ''}",
            "",
            f"{row['reply'] or '_(empty)_'}",
            "",
            "**Fine-tuned**",
            "",
            f"{other.get('reply') or '_(empty)_'}",
            "",
        ]

    path.write_text("\n".join(lines), encoding="utf-8")


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:8080/v1")
    ap.add_argument("--base-model", default="base")
    ap.add_argument("--tuned-url", default="http://127.0.0.1:8081/v1")
    ap.add_argument("--tuned-model", default="finetuned")
    ap.add_argument("--eval-set", default=str(HERE / "eval_set.jsonl"))
    ap.add_argument("--out-dir", default=str(HERE / "results"))
    ap.add_argument("--max-tokens", type=int, default=220)
    ap.add_argument("--temperature", type=float, default=0.6)
    ap.add_argument(
        "--runs", type=int, default=3,
        help="Generations per case. At temperature>0 a single run gives "
             "categories with n=2-3, where one unlucky sample swings a rate by "
             "33 points. Use 5+ for anything quoted in the report.",
    )
    ap.add_argument("--base-only", action="store_true",
                    help="Run only the base checkpoint (before M8 lands)")
    args = ap.parse_args()

    cases = [
        json.loads(line)
        for line in Path(args.eval_set).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    print(f"Loaded {len(cases)} cases from {Path(args.eval_set).name}")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"\nRunning base ({args.base_model})…")
    base_rows = await run_variant("base", args.base_url, args.base_model, cases, args)

    if args.base_only:
        tuned_rows: list[dict] = []
        print("\n--base-only: skipping the fine-tuned checkpoint.")
    else:
        print(f"\nRunning fine-tuned ({args.tuned_model})…")
        tuned_rows = await run_variant(
            "tuned", args.tuned_url, args.tuned_model, cases, args
        )

    (out_dir / "results.json").write_text(
        json.dumps({"base": base_rows, "tuned": tuned_rows}, indent=2),
        encoding="utf-8",
    )

    print("\n" + "=" * 72)
    print("BEHAVIOURAL CHECKS")
    print("=" * 72)
    base_s = score(base_rows)
    tuned_s = score(tuned_rows) if tuned_rows else {}
    print(f"  {'check':<38} {'base':>8} {'tuned':>8}   n")
    print("  " + "-" * 62)
    for check in CHECKS:
        bp, ba = base_s[check.key]
        tp, ta = tuned_s.get(check.key, (0, 0))
        if ba == 0 and ta == 0:
            continue
        print(f"  {check.label:<38} {pct(bp, ba):>8} {pct(tp, ta):>8}   {max(ba, ta)}")

    if tuned_rows:
        write_report(base_rows, tuned_rows, args, out_dir / "report.md")
        print(f"\n  wrote {out_dir / 'report.md'}")
    print(f"  wrote {out_dir / 'results.json'}")

    if not tuned_rows:
        print("\n  Base-only run. Re-run without --base-only once M8's checkpoint")
        print("  is served to produce the comparison the rubric requires.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
