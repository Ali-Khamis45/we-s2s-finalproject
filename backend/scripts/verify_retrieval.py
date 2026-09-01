"""Verification of the retrieval path (A9, A11).

Uses a THROWAWAY TEST FIXTURE corpus, not data/corpus. Nothing here is a real
source and none of it belongs in the thesis — the real knowledge base needs
attributable, non-clinical documents logged in data/corpus/SOURCES.md (A10).

What is under test:
  1. Ingestion chunks, embeds, and indexes documents.
  2. An in-corpus question retrieves relevant, cited material.
  3. MMR returns diverse chunks rather than near-duplicates.
  4. THE GROUNDEDNESS GATE: an out-of-corpus question returns nothing, so the
     coach says it has no material instead of inventing technique advice.
     Being unhelpful about technique is far better than being confidently wrong.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="scc-rag-"))
CORPUS = TMP / "corpus"
CORPUS.mkdir(parents=True)

os.environ["SCC_DATA_DIR"] = str(TMP)
os.environ["SCC_CORPUS_DIR"] = str(CORPUS)
os.environ["SCC_CHROMA_DIR"] = str(TMP / "chroma")
os.environ["SCC_DATABASE_URL"] = f"sqlite+aiosqlite:///{(TMP / 't.db').as_posix()}"

# --- test fixture documents (NOT real sources) ---
(CORPUS / "pacing.md").write_text(
    """# Pacing and Rate Control

Speaking rate is one of the few things a speaker can adjust consciously in the
moment. Most people speed up under pressure, and a faster rate leaves less time
to plan the next phrase.

A useful drill is the short-first-sentence habit. Deliberately make the opening
sentence of any answer short. It buys planning time for the sentences that
follow and sets a slower baseline for the rest of the turn.

Pausing deliberately at phrase boundaries also helps. A planned pause reads as
confidence to a listener, while an unplanned one reads as hesitation, even when
they are the same length.
""",
    encoding="utf-8",
)

(CORPUS / "presentations.md").write_text(
    """# Preparing a Presentation

Rehearsing out loud matters more than rereading slides. Silent review skips the
motor planning that speaking actually requires.

Rehearse the opening more than the middle. The first thirty seconds carry the
most anxiety, and having them automatic frees attention for everything after.

Practise answering questions, not only delivering material. Question handling is
where preparation usually stops and where most speakers feel least secure.
""",
    encoding="utf-8",
)

(CORPUS / "fillers.md").write_text(
    """# Filler Words

Filler words such as "um" and "uh" are normal features of spontaneous speech.
Everyone produces them, and eliminating them entirely is neither realistic nor
necessary.

If reducing fillers is a goal, the effective substitution is silence. Replacing
a filler with a brief pause sounds more composed and is easier than suppressing
the filler outright.

Recording yourself once a week is usually enough to notice the pattern. Counting
fillers in every practice session tends to increase self-monitoring without
improving delivery.
""",
    encoding="utf-8",
)


async def main() -> int:
    from app.core.config import settings
    from app.services.ingestion import ingestion_service
    from app.services.retrieval import retrieval_service

    failures: list[str] = []

    def check(label: str, ok: bool, detail: str = "") -> None:
        print(f"  {'PASS' if ok else 'FAIL'}  {label}{('  — ' + detail) if detail else ''}")
        if not ok:
            failures.append(label)

    print("=" * 72)
    print("INGESTION")
    print("=" * 72)
    report = await ingestion_service.ingest_directory()
    print(f"  files   {report.files}")
    print(f"  chunks  {report.chunks}")
    print(f"  sources {report.sources}")
    if report.skipped:
        print(f"  skipped {report.skipped}")

    total = await retrieval_service.count()
    check("documents were indexed", total > 0, f"{total} chunks")
    check("all three files ingested", report.files == 3, f"{report.files}")

    print("\n" + "=" * 72)
    print("IN-CORPUS RETRIEVAL")
    print("=" * 72)
    q1 = "How do I stop speaking too fast when I get nervous?"
    print(f"  query: {q1!r}\n")
    r1 = await retrieval_service.retrieve(q1)
    print(f"  grounded    {r1.grounded}")
    print(f"  best score  {r1.best_score:.4f}  (gate at {settings.retrieval_min_score})")
    for c in r1.citations:
        head = c.excerpt.replace("\n", " ")[:88]
        print(f"    [{c.score:.3f}] {c.source:<20} {head}…")

    check("in-corpus question is grounded", r1.grounded)
    check("citations returned", len(r1.citations) > 0, f"{len(r1.citations)}")
    check(
        "retrieved the pacing document",
        any("pacing" in c.source for c in r1.citations),
        ", ".join(sorted({c.source for c in r1.citations})),
    )

    print("\n" + "=" * 72)
    print("MMR DIVERSITY")
    print("=" * 72)
    q2 = "advice for speaking practice"
    r2 = await retrieval_service.retrieve(q2, k=3)
    sources = [c.source for c in r2.citations]
    print(f"  query: {q2!r}")
    print(f"  sources returned: {sources}")
    check(
        "MMR returns more than one document",
        len(set(sources)) > 1,
        f"{len(set(sources))} distinct",
    )

    print("\n" + "=" * 72)
    print("GROUNDEDNESS GATE  (the honest part)")
    print("=" * 72)
    for q in (
        "What is the capital city of Mongolia?",
        "How do I change the oil filter in a diesel engine?",
    ):
        r = await retrieval_service.retrieve(q)
        print(f"\n  query: {q!r}")
        print(f"  grounded {r.grounded}   best score {r.best_score:.4f}   "
              f"citations {len(r.citations)}")
        check(
            "out-of-corpus question is refused, not answered from weak matches",
            not r.grounded and len(r.citations) == 0,
            f"score {r.best_score:.3f} < gate {settings.retrieval_min_score}",
        )

    print("\n" + "=" * 72)
    print(f"{'All checks passed.' if not failures else str(len(failures)) + ' failed'}")
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
