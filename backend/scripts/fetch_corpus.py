"""Fetch a public-domain starter corpus for the knowledge base (task A10).

`data/corpus/` is gitignored, so THIS SCRIPT is the reproducible record of what
the knowledge base contains. Running it recreates the corpus exactly; the
provenance table it prints goes into data/corpus/SOURCES.md.

Sources are Project Gutenberg texts on public speaking and vocal delivery, all
published before 1930 and in the public domain in the United States. Every one
is a real, attributable work with a named author and a stable identifier.

    THIS IS A STARTER CORPUS, NOT A FINISHED ONE.

    These books are a century old. They are genuinely useful on the mechanics
    the project cares about — pausing, pace, breathing, monotony, stage fright,
    which have not changed — but they are dated in vocabulary and in places in
    attitude, and they are not modern evidence-based communication coaching.
    Supplement or replace them with current, properly licensed material before
    the work is submitted, and record every addition in SOURCES.md.

SCOPE SCREENING (docs/ETHICS.md): the project is an accessibility and practice
tool, explicitly non-clinical. Period elocution texts sometimes contain chapters
on "curing" stammering, written with attitudes that are both clinical and
demeaning by any modern standard. Nothing like that may enter a corpus that
feeds coaching answers, so every download is scanned for clinical language and
anything above threshold is rejected with its hits reported.

Usage:
    python scripts/fetch_corpus.py            # download, screen, write
    python scripts/fetch_corpus.py --dry-run  # screen only, write nothing
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[2]
CORPUS_DIR = REPO_ROOT / "data" / "corpus"

HEADERS = {"User-Agent": "speech-confidence-coach/0.1 (graduation project)"}


@dataclass(frozen=True)
class Source:
    gutenberg_id: int
    slug: str
    title: str
    author: str
    year: str
    why: str

    @property
    def url(self) -> str:
        return (
            f"https://www.gutenberg.org/cache/epub/"
            f"{self.gutenberg_id}/pg{self.gutenberg_id}.txt"
        )


#: Excluded after reading the screen's flagged passages. The automated check
#: NARROWS the field; a person decides. Keep this list and its reasons — the
#: report's ethics section needs to show the process actually rejected
#: something, not merely that it existed.
DENIED: dict[int, str] = {
    17318: (
        "Clarence Stratton, 'Public Speaking' (1920). Passed the clustering "
        "screen at 5/8, but reading the passages shows why it cannot be used: "
        "it claims an 'inveterate stammerer, stutterer, or repeater can be "
        "relieved, if not cured, of the embarrassing impediment by attention "
        "to the position of the vocal organs', and refers to 'the dumb'. That "
        "is a false cure claim, clinically framed, about precisely the people "
        "this tool serves. Retrieved into an answer it would be actively "
        "harmful, and it violates the non-clinical scope in docs/ETHICS.md."
    ),
}


#: Chosen for practical delivery guidance — pacing, pausing, breathing, nerves —
#: rather than for rhetoric or for anthologies of speeches to recite.
SOURCES: tuple[Source, ...] = (
    Source(16317, "art-of-public-speaking", "The Art of Public Speaking",
           "J. Berg Esenwein and Dale Carnegie", "1915",
           "The canonical text. Strong chapters on pause, pace, monotony and stage fright."),
    Source(17318, "public-speaking-stratton", "Public Speaking",
           "Clarence Stratton", "1920",
           "A practical university textbook; structure, delivery and preparation."),
    Source(18095, "successful-methods", "Successful Methods of Public Speaking",
           "Grenville Kleiser", "1920",
           "Short, concrete drills — closest in form to the exercises the coach suggests."),
    Source(18277, "training-of-a-speaker", "The Training of a Public Speaker",
           "Grenville Kleiser", "1920",
           "Practice regimens and self-assessment routines."),
    Source(53869, "how-to-become-a-speaker", "How to Become a Public Speaker",
           "William Pittenger", "1900",
           "Beginner-oriented; addresses nervousness directly."),
    Source(31828, "vocal-expression", "Vocal Expression",
           "Katherine Jewell Everts", "1919",
           "Voice, breath and phrasing — the acoustic side of delivery."),
)

# ---- scope screening -------------------------------------------------

#: Terms that actually pathologise a speaker. These are the ones that matter.
#:
#: An earlier version of this screen also counted "cure", "remedy", "patient",
#: "treatment", "defect" and "disease", and rejected four of six books. Reading
#: the hits showed why that was wrong: in 1915 prose those words are ordinary
#: register — "the treatment of the subject", "patient effort", "a remedy for
#: monotony", "defects of delivery". The screen was measuring period English,
#: not clinical framing.
PATHOLOGISING = (
    "stammer", "stutter", "impediment", "affliction",
    "infirmity", "deformity", "malady",
)

#: A passing mention is fine and unavoidable in period texts. A sustained
#: passage — a chapter on "curing stammering" — is not, and that is what has to
#: be caught. So the screen looks for CLUSTERING rather than raw frequency:
#: the densest window in the text, not its average.
WINDOW_WORDS = 2_000
WINDOW_LIMIT = 8

START_RE = re.compile(r"\*\*\*\s*START OF (?:THE|THIS) PROJECT GUTENBERG.*?\*\*\*", re.I)
END_RE = re.compile(r"\*\*\*\s*END OF (?:THE|THIS) PROJECT GUTENBERG.*?\*\*\*", re.I)


@dataclass
class Screened:
    source: Source
    words: int = 0
    hits: dict[str, int] = field(default_factory=dict)
    worst_window: int = 0
    contexts: list[str] = field(default_factory=list)
    accepted: bool = False
    body: str = ""

    @property
    def total_hits(self) -> int:
        return sum(self.hits.values())


def strip_boilerplate(raw: str) -> str:
    """Remove the Gutenberg header and licence.

    The licence must not survive into the corpus: it is several hundred words
    of legal text that would be chunked, embedded, and eventually retrieved as
    though it were coaching advice.
    """
    if m := START_RE.search(raw):
        raw = raw[m.end():]
    if m := END_RE.search(raw):
        raw = raw[: m.start()]
    # Collapse the hard-wrapped source into paragraphs so the chunker splits on
    # meaning rather than on line breaks.
    raw = re.sub(r"[ \t]+\n", "\n", raw)
    raw = re.sub(r"(?<!\n)\n(?!\n)", " ", raw)
    raw = re.sub(r"\n{3,}", "\n\n", raw)
    return raw.strip()


def screen(source: Source, body: str) -> Screened:
    """Reject texts that dwell on speech pathology; report the rest for review."""
    words = body.split()
    low_words = [w.lower() for w in words]
    pattern = re.compile(rf"\b({'|'.join(PATHOLOGISING)})\w*", re.I)

    hits: dict[str, int] = {}
    positions: list[int] = []
    for i, w in enumerate(low_words):
        if m := pattern.match(w):
            term = next(t for t in PATHOLOGISING if m.group(1).lower().startswith(t))
            hits[term] = hits.get(term, 0) + 1
            positions.append(i)

    # Densest window: the strongest signal that a whole passage is about this.
    worst = 0
    for start in positions:
        n = sum(1 for p in positions if start <= p < start + WINDOW_WORDS)
        worst = max(worst, n)

    # Keep a little context around each hit so a human can actually judge it,
    # rather than trusting a keyword count.
    contexts = [
        " ".join(words[max(0, p - 12) : p + 13]).replace("\n", " ")
        for p in positions[:6]
    ]

    return Screened(
        source=source,
        words=len(words),
        hits=hits,
        worst_window=worst,
        contexts=contexts,
        accepted=worst < WINDOW_LIMIT,
        body=body,
    )


def _wrap(text: str, width: int) -> list[str]:
    import textwrap

    return textwrap.wrap(text, width=width)


def fetch(source: Source) -> str:
    r = httpx.get(source.url, headers=HEADERS, timeout=120, follow_redirects=True)
    r.raise_for_status()
    return r.text


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="screen only, write nothing")
    args = ap.parse_args()

    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    results: list[Screened] = []

    print("=" * 78)
    print("FETCHING AND SCREENING")
    print("=" * 78)

    for source in SOURCES:
        print(f"\n  #{source.gutenberg_id}  {source.title}")

        if reason := DENIED.get(source.gutenberg_id):
            print("    EXCLUDED after human review of the screen's output:")
            for line in _wrap(reason, 70):
                print(f"      {line}")
            continue

        try:
            raw = fetch(source)
        except Exception as exc:
            print(f"    DOWNLOAD FAILED: {exc}")
            continue
        time.sleep(0.5)

        body = strip_boilerplate(raw)
        result = screen(source, body)
        results.append(result)

        print(f"    {result.words:,} words   "
              f"{result.total_hits} pathologising mention(s)   "
              f"densest window {result.worst_window}/{WINDOW_LIMIT}")
        if result.hits:
            top = sorted(result.hits.items(), key=lambda kv: -kv[1])
            print(f"    terms: {', '.join(f'{t} x{n}' for t, n in top)}")
            for ctx in result.contexts:
                print(f"      … {ctx[:110]} …")

        if not result.accepted:
            print("    REJECTED — a sustained passage about speech pathology. "
                  "Out of scope for a non-clinical corpus.")
            continue

        if args.dry_run:
            print("    accepted (dry run, not written)")
            continue

        path = CORPUS_DIR / f"{source.slug}.txt"
        path.write_text(
            f"# {source.title}\n\n{result.body}\n", encoding="utf-8"
        )
        print(f"    written -> data/corpus/{path.name}")

    accepted = [r for r in results if r.accepted]
    rejected = [r for r in results if not r.accepted]

    print("\n" + "=" * 78)
    print("SUMMARY")
    print("=" * 78)
    print(f"  accepted {len(accepted)}   rejected {len(rejected)}   "
          f"total words {sum(r.words for r in accepted):,}")

    if rejected:
        print("\n  rejected by the scope screen:")
        for r in rejected:
            print(f"    #{r.source.gutenberg_id} {r.source.title} "
                  f"(densest window {r.worst_window})")

    flagged = [r for r in accepted if r.total_hits]
    if flagged:
        print("\n  ACCEPTED BUT WORTH READING — these passed on clustering, but")
        print("  contain passing mentions. Skim the contexts printed above; if any")
        print("  reads as pathologising, drop the book rather than keeping it.")
        for r in flagged:
            print(f"    #{r.source.gutenberg_id} {r.source.title}: "
                  f"{r.total_hits} mention(s)")

    print("\n" + "=" * 78)
    print("PROVENANCE  — paste into data/corpus/SOURCES.md")
    print("=" * 78)
    print("\n| # | Title | Author | Year | Type | Licence | Gutenberg |")
    print("|---|---|---|---|---|---|---|")
    for i, r in enumerate(accepted, start=1):
        s = r.source
        print(f"| {i} | {s.title} | {s.author} | {s.year} | Book | "
              f"Public domain (US) | [#{s.gutenberg_id}]"
              f"(https://www.gutenberg.org/ebooks/{s.gutenberg_id}) |")

    if not args.dry_run and accepted:
        print("\n  Next: POST /api/corpus/ingest, then re-run "
              "scripts/verify_retrieval.py to recalibrate the groundedness gate\n"
              "  against real content — the current 0.55 was tuned on a fixture.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
