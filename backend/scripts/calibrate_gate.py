"""Calibrate the groundedness gate against the real corpus (task A10 follow-up).

`SCC_RETRIEVAL_MIN_SCORE` decides when the coach admits it has no material
instead of answering from weak matches. It is specific to the corpus AND the
embedding model, so it has to be re-derived whenever either changes — a value
carried over from a different corpus is a guess wearing a number.

This ingests `data/corpus/` for real, then scores two sets of questions: ones
the corpus genuinely covers, and ones it clearly does not. A usable threshold
sits in the gap between them. If there is no gap, the report should say so
rather than pretending a single number separates the two.

Usage:
    python scripts/calibrate_gate.py             # reuse existing index
    python scripts/calibrate_gate.py --reingest  # rebuild from data/corpus
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys

IN_CORPUS = [
    "How do I stop speaking too fast when I get nervous?",
    "What should I do with my hands while speaking?",
    "How can I use pauses more effectively?",
    "How do I stop sounding monotonous?",
    "What is the best way to prepare an opening?",
    "How should I breathe while speaking?",
    "How do I deal with stage fright before a talk?",
    "How can I make my voice carry in a large room?",
    "What makes a speaker sound confident?",
    "How do I keep an audience's attention?",
]

OUT_OF_CORPUS = [
    "How do I change the oil filter in a diesel engine?",
    "What is the capital city of Mongolia?",
    "Write me a Python function to sort a list.",
    "What is the best fertiliser for tomatoes?",
    "How do I refinance a mortgage?",
    "Who won the World Cup in 1998?",
    "What is the melting point of tungsten?",
    "How do I train for a marathon?",
]


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reingest", action="store_true")
    args = ap.parse_args()

    from app.core.config import settings
    from app.services.ingestion import ingestion_service
    from app.services.retrieval import retrieval_service

    if args.reingest:
        print("Ingesting data/corpus (this takes a few minutes on CPU)…")
        report = await ingestion_service.ingest_directory(reset=True)
        print(f"  {report.files} files, {report.chunks} chunks")
        if report.skipped:
            print(f"  skipped: {report.skipped}")

    total = await retrieval_service.count()
    print(f"\nIndex: {total} chunks   embedder: {settings.embedding_model}")
    if total == 0:
        print("Corpus is empty. Run scripts/fetch_corpus.py, then re-run with --reingest.")
        return 1

    async def score(q: str) -> tuple[float, str]:
        # Bypass the gate: we need the RAW best score to calibrate it.
        vec = (await retrieval_service.embed([q], is_query=True))[0]
        collection = await retrieval_service.collection()
        raw = await asyncio.to_thread(
            collection.query,
            query_embeddings=[vec.tolist()],
            n_results=1,
            include=["distances", "metadatas"],
        )
        dists = raw.get("distances") or [[]]
        metas = raw.get("metadatas") or [[]]
        if not dists[0]:
            return 0.0, "—"
        best = 1.0 - float(dists[0][0])
        src = str((metas[0][0] or {}).get("source", "?"))
        return best, src

    print("\n" + "=" * 78)
    print("IN CORPUS  (should score HIGH)")
    print("=" * 78)
    in_scores = []
    for q in IN_CORPUS:
        s, src = await score(q)
        in_scores.append(s)
        print(f"  {s:.3f}  {q[:52]:<52} {src[:22]}")

    print("\n" + "=" * 78)
    print("OUT OF CORPUS  (should score LOW)")
    print("=" * 78)
    out_scores = []
    for q in OUT_OF_CORPUS:
        s, src = await score(q)
        out_scores.append(s)
        print(f"  {s:.3f}  {q[:52]:<52} {src[:22]}")

    lo_in, hi_out = min(in_scores), max(out_scores)
    print("\n" + "=" * 78)
    print("SEPARATION")
    print("=" * 78)
    print(f"  in-corpus     min {lo_in:.3f}   median {statistics.median(in_scores):.3f}"
          f"   max {max(in_scores):.3f}")
    print(f"  out-of-corpus min {min(out_scores):.3f}   "
          f"median {statistics.median(out_scores):.3f}   max {hi_out:.3f}")
    print(f"  current gate  {settings.retrieval_min_score}")

    print()
    if lo_in > hi_out:
        # Midpoint of the gap, biased toward the out-of-corpus side: a false
        # refusal costs one unhelpful answer, a false accept costs a confident
        # wrong one about speech technique. The asymmetry is deliberate.
        suggested = round(hi_out + (lo_in - hi_out) * 0.4, 2)
        print(f"  CLEAN SEPARATION: gap of {lo_in - hi_out:.3f} between the sets.")
        print(f"  Suggested SCC_RETRIEVAL_MIN_SCORE = {suggested}")
        print("  (placed nearer the out-of-corpus side on purpose — a wrong answer")
        print("   about technique is worse than an unhelpful one.)")
    else:
        overlap_in = [s for s in in_scores if s <= hi_out]
        overlap_out = [s for s in out_scores if s >= lo_in]
        print(f"  OVERLAP of {hi_out - lo_in:.3f}. No single threshold separates these.")
        print(f"    {len(overlap_in)} in-corpus question(s) score at or below the "
              f"highest out-of-corpus score")
        print(f"    {len(overlap_out)} out-of-corpus question(s) score at or above "
              f"the lowest in-corpus score")
        print("\n  Pick the threshold by which error you would rather make, and SAY SO")
        print("  in the report. Raising it refuses more real questions; lowering it")
        print("  lets more invented answers through.")

    current = settings.retrieval_min_score
    fp = sum(1 for s in out_scores if s >= current)
    fn = sum(1 for s in in_scores if s < current)
    print(f"\n  At the current gate of {current}:")
    print(f"    {fp}/{len(out_scores)} out-of-corpus questions would be ANSWERED "
          f"(should be 0)")
    print(f"    {fn}/{len(in_scores)} in-corpus questions would be REFUSED "
          f"(should be 0)")

    await retrieval_service.embedder()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
