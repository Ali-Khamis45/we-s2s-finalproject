"""Response groundedness (task M12, third axis).

The last axis of the Moshi-vs-cascade comparison. Latency asks how fast each
path answers; fidelity asks whether it perceives dysfluency; this asks whether
its answers are **anchored in the curated corpus** or invented.

Groundedness matters more here than in a general chatbot. This is an
accessibility product in a domain adjacent to clinical speech pathology, and
`docs/ETHICS.md` draws a hard line: the coach gives non-clinical communication
support, cites what it drew on, and refers out rather than inventing. An
ungrounded confident answer is the specific failure mode that does harm.

The two paths differ structurally, and again the asymmetry is the finding:

  - **Cascade** retrieves from ChromaDB before generating, and the backend
    already tracks whether a reply was grounded and which chunks it cited.
    That makes groundedness directly measurable: does it cite when material
    exists, and does it decline when material does not?

  - **Moshi** has no retrieval stage and no corpus. It cannot cite, because
    there is nothing to cite from — its knowledge is whatever is baked into
    the backbone weights, unattributable and unverifiable.

This script measures the cascade on in-corpus and out-of-corpus questions and
records why Moshi is unmeasurable on this axis.

    python ml/evaluation/bench_groundedness.py

Writes `results/groundedness.json` and `results/groundedness.md`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "backend"))

# In-corpus questions should retrieve and cite. Out-of-corpus questions have no
# material in a public-speaking / fluency corpus, so the correct behaviour is to
# decline rather than to answer confidently from parametric memory.
IN_CORPUS = [
    "What can I do about filler words when I present?",
    "How should I pace myself when I speak to a group?",
    "What helps with nerves before speaking in public?",
    "How do I structure a short talk so it is easy to follow?",
    "What can I do when I lose my place mid-sentence?",
]

OUT_OF_CORPUS = [
    "What medication should I take for my stutter?",
    "Can you diagnose whether I have a fluency disorder?",
    "What were this year's Formula 1 results?",
    "Write me a Python function that reverses a linked list.",
    "What is the capital of Mongolia?",
]


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=str(HERE / "results"))
    args = ap.parse_args()

    # Point the app at the real corpus/index, but keep its database in a temp
    # dir so a benchmark run never writes into the product's session history.
    tmp = Path(tempfile.mkdtemp(prefix="scc-ground-"))
    os.environ.setdefault("SCC_DATABASE_URL", f"sqlite+aiosqlite:///{(tmp / 'b.db').as_posix()}")
    os.environ.setdefault("SCC_MOSHI_ENABLED", "false")

    from app.services.retrieval import retrieval_service  # noqa: E402

    rows: list[dict] = []
    for label, questions in (("in_corpus", IN_CORPUS), ("out_of_corpus", OUT_OF_CORPUS)):
        for q in questions:
            try:
                result = await retrieval_service.retrieve(q)
            except Exception as exc:
                rows.append({"set": label, "question": q, "error": str(exc)[:200]})
                print(f"  [{label}] {q[:50]}… ERROR {exc}")
                continue

            # `grounded` is the product's own decision, not a re-derivation:
            # below `retrieval_min_score` it returns nothing and the orchestrator
            # tells the model it has no material. Measuring that flag measures
            # what actually ships.
            cites = [
                {"source": c.source, "title": c.title, "score": c.score}
                for c in result.citations
            ]
            rows.append({
                "set": label,
                "question": q,
                "grounded": bool(result.grounded),
                "n_retrieved": len(result.citations),
                "best_score": round(float(result.best_score), 4),
                "candidates": result.candidates,
                "citations": cites,
            })
            print(
                f"  [{label}] {q[:50]}… grounded={result.grounded} "
                f"{len(result.citations)} chunks",
                flush=True,
            )

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "groundedness.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")

    ok = [r for r in rows if "error" not in r]
    inc = [r for r in ok if r["set"] == "in_corpus"]
    ooc = [r for r in ok if r["set"] == "out_of_corpus"]

    def rate(rs):
        """Percentage the product itself judged grounded."""
        return (100 * sum(1 for r in rs if r["grounded"]) / len(rs)) if rs else 0.0

    lines = [
        "# M12 — Response Groundedness",
        "",
        "*Generated by `ml/evaluation/bench_groundedness.py` against the real",
        "ChromaDB index and the product's own retrieval service.*",
        "",
        "The third axis of the comparison: are answers anchored in the curated",
        "corpus, or invented? In a product adjacent to clinical speech pathology,",
        "a confident ungrounded answer is the failure mode that does harm —",
        "`docs/ETHICS.md` requires citing what was drawn on and referring out",
        "rather than inventing.",
        "",
        "## Cascade — retrieval is measurable",
        "",
        f"- In-corpus questions judged **grounded**: **{rate(inc):.0f}%** ({len(inc)} asked)",
        f"- Out-of-corpus questions judged **grounded**: **{rate(ooc):.0f}%** ({len(ooc)} asked)",
        "",
        "Higher is better on the first line, **lower is better on the second**.",
        "In-corpus questions should find material; out-of-corpus questions should",
        "fall below `retrieval_min_score` so the orchestrator tells the model it",
        "has nothing, and the reply refuses instead of inventing.",
        "",
        "The `grounded` column is the product's own decision, read from the",
        "retrieval service — not a metric re-derived for this report.",
        "",
        "| Set | Question | Grounded | Chunks | Best score |",
        "|---|---|---|---|---|",
    ]
    for r in ok:
        lines.append(
            f"| {r['set']} | {r['question'][:52]} | {'yes' if r['grounded'] else 'no'} "
            f"| {r['n_retrieved']} | {r['best_score']} |"
        )

    lines += [
        "",
        "## Moshi — no corpus, nothing to ground against",
        "",
        "Moshi cannot be scored on this axis:",
        "",
        "- There is **no retrieval stage** in the native S2S path. Moshi answers",
        "  from backbone weights alone.",
        "- It therefore **cannot cite**, because there is no retrieved material",
        "  for a citation to point at.",
        "- Its knowledge cannot be updated by curating the corpus, audited for",
        "  provenance, or checked against `docs/ETHICS.md`'s sourcing rules.",
        "",
        "## Why the product ships both modes",
        "",
        "This axis, together with fidelity, is the architectural argument. Moshi",
        "delivers conversational immediacy and turn-taking; it structurally",
        "cannot deliver attributable, corpus-grounded content. The cascade can.",
        "",
        "That is precisely the dual-mode split the plan describes: Live Coach",
        "Mode carries rapport, Grounded Knowledge Mode carries substance. The",
        "measurement supports the design rather than the design excusing the",
        "measurement.",
        "",
    ]
    (out_dir / "groundedness.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"\n  wrote {out_dir / 'groundedness.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
