# Knowledge Base Sources

*Task A10. Every document ingested into the RAG corpus is logged here.*

The corpus files themselves are gitignored — this file plus
[`backend/scripts/fetch_corpus.py`](../../backend/scripts/fetch_corpus.py) are the
committed, reproducible record of what the knowledge base contains. Running that script
recreates the corpus exactly. The report's sources appendix comes from the table below.

## Inclusion rules

1. **Non-clinical only.** Public-speaking guidance, delivery and voice technique,
   communication-skills material. No treatment protocols, no diagnostic criteria, no
   claims about curing anything. See [ETHICS.md](../../docs/ETHICS.md).
2. **Licence must permit use.** Record it. Anything unclear does not go in.
3. **Attributable.** A document with no identifiable author or publisher is not
   admissible as a retrieval source for a coaching claim.

---

## Corpus log

Public-domain works retrieved from Project Gutenberg, all published before 1930 and in
the public domain in the United States.

| # | Title | Author | Year | Words | Licence | Source |
|---|---|---|---|---|---|---|
| 1 | The Art of Public Speaking | J. Berg Esenwein and Dale Carnegie | 1915 | 159,406 | Public domain (US) | [Gutenberg #16317](https://www.gutenberg.org/ebooks/16317) |
| 2 | Vocal Expression | Katherine Jewell Everts | 1919 | 62,339 | Public domain (US) | [Gutenberg #31828](https://www.gutenberg.org/ebooks/31828) |
| 3 | How to Become a Public Speaker | William Pittenger | 1900 | 40,181 | Public domain (US) | [Gutenberg #53869](https://www.gutenberg.org/ebooks/53869) |
| 4 | The Training of a Public Speaker | Grenville Kleiser | 1920 | 31,225 | Public domain (US) | [Gutenberg #18277](https://www.gutenberg.org/ebooks/18277) |
| 5 | Successful Methods of Public Speaking | Grenville Kleiser | 1920 | 20,715 | Public domain (US) | [Gutenberg #18095](https://www.gutenberg.org/ebooks/18095) |

**Total: 313,866 words → 1,057 chunks.** Gutenberg licence boilerplate is stripped before
ingestion; left in, it would be chunked, embedded, and eventually retrieved as though it
were coaching advice.

---

## Excluded after review

The scope screen in `fetch_corpus.py` flags pathologising language and a human decides.
It rejected one book, and the reason is worth stating in the report's ethics section —
it demonstrates the process did something rather than merely existing.

**Clarence Stratton, *Public Speaking* (1920), [Gutenberg #17318](https://www.gutenberg.org/ebooks/17318) — excluded.**

It passed the automated clustering check, but reading the flagged passages showed it
claims an *"inveterate stammerer, stutterer, or repeater can be relieved, if not cured,
of the embarrassing impediment by attention to the position of the vocal organs"*, and
refers to *"the dumb"*. That is a false cure claim, clinically framed, about precisely
the people this tool is built for. Retrieved into an answer it would be actively harmful.

Three accepted books contain passing mentions of stammering — Demosthenes anecdotes and
descriptive uses ("sentences are stammered out"). These were read and judged acceptable:
none is a cure claim and none prescribes anything. The contexts are printed by
`fetch_corpus.py` on every run if you want to re-examine that judgement.

---

## Limitations to state in the report

**This is a starter corpus and should not be the final one.** These books are a century
old. They are genuinely good on the mechanics the project cares about — pausing, pace,
breathing, monotony, stage fright — which have not changed. But:

- The vocabulary is dated, and so in places are the attitudes.
- They predate all modern evidence-based communication research.
- They address oratory and elocution, not the accessibility framing this project uses.
- Coverage is uneven: over half the corpus is one book, so retrieval leans on Esenwein
  and Carnegie more than is ideal.

Supplement or replace with current, properly licensed material before submission, and
add every new document to the table above.

---

## Ingestion parameters

Recorded so the report can state them and re-ingestion is reproducible.

- **Chunk size:** 512 tokens, 64-token overlap
- **Splitter:** LangChain recursive character splitter
- **Embedding model:** `BAAI/bge-small-en-v1.5`
- **Vector store:** ChromaDB, cosine, persisted to `data/chroma/`
- **Retrieval:** MMR, `k=4`, `fetch_k=20`, `lambda=0.5`

Rebuild with `POST /api/corpus/ingest?reset=true`. Last rebuilt: **1,057 chunks from 5
files.**

---

## Recalibrate the groundedness gate whenever the corpus changes

`SCC_RETRIEVAL_MIN_SCORE` (currently **0.65**) decides when the coach admits it has no
material rather than answering from weak matches. It is specific to this corpus *and*
this embedding model.

**Corpus size moves it.** A threshold of 0.55, derived from a three-document fixture,
would have answered 3 of 8 out-of-corpus questions once these books were indexed — a
larger corpus offers more chances for a spurious high match. Measured over the current
corpus:

| Set | min | median | max |
|---|---|---|---|
| In corpus (10 questions) | 0.696 | 0.720 | 0.814 |
| Out of corpus (8 questions) | 0.434 | 0.532 | 0.619 |

0.65 sits in the 0.077 gap, placed nearer the out-of-corpus side on purpose: refusing a
real question costs one unhelpful turn, while accepting a false one puts confident wrong
technique advice in front of someone practising their speech.

Re-derive with:

```bash
python backend/scripts/calibrate_gate.py --reingest
```
