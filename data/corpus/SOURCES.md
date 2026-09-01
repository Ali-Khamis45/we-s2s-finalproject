# Knowledge Base Sources

*Task A10. Every document ingested into the RAG corpus is logged here.*

The corpus files themselves are gitignored — this file is the committed record of what the
knowledge base contains and where each item came from. The final report's sources appendix
is generated from this table.

## Inclusion rules

1. **Non-clinical only.** Public-speaking guidance, fluency-shaping *technique*
   descriptions, communication-skills material, accessibility guidance. No treatment
   protocols, no diagnostic criteria, no clinical literature. See [ETHICS.md](../../docs/ETHICS.md).
2. **Licence must permit use.** Record it. Anything unclear does not go in.
3. **Attributable.** A document with no identifiable author or publisher is not admissible
   as a retrieval source for a coaching claim.

## Corpus log

| # | Title | Author / Publisher | Year | Type | Licence | Retrieved | Notes |
|---|---|---|---|---|---|---|---|
| | *(add entries as documents are ingested)* | | | | | | |

## Ingestion parameters

Recorded here so the report can state them and so re-ingestion is reproducible.

- **Chunk size:** 512 tokens, 64-token overlap
- **Splitter:** semantic / recursive character
- **Embedding model:** `BAAI/bge-small-en-v1.5`
- **Vector store:** ChromaDB, persisted to `data/chroma/`
- **Retrieval:** MMR, `k=4`

Re-run ingestion after any change to this list, and note the date the store was last
rebuilt: *(not yet built)*
