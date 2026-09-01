"""Retrieval (A11) — ChromaDB with MMR reranking and a groundedness gate.

Two details matter more than the plumbing.

MMR is implemented here rather than delegated. Chroma's own MMR helper does not
hand back relevance scores, and this pipeline needs both: the scores drive the
groundedness gate and the citation display, while the diversity term stops four
near-identical chunks from one document crowding out the rest of the corpus.
One query returns embeddings, documents, metadata and distances, and the
reranking runs over that.

The gate is the honest part. When the best match falls below
`retrieval_min_score`, this returns nothing and the orchestrator tells the model
it has no material — which is how "ask something outside the corpus and get a
graceful refusal instead of a hallucination" (verification step 4) actually
holds. Being unhelpful about a technique beats being confidently wrong about one.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import numpy as np

from app.core.config import settings
from app.core.errors import CorpusEmptyError, DependencyMissingError
from app.core.logging import get_logger
from app.schemas.chat import Citation

log = get_logger(__name__)


@dataclass(slots=True)
class RetrievalResult:
    citations: list[Citation]
    grounded: bool
    best_score: float = 0.0
    candidates: int = 0

    @property
    def is_empty(self) -> bool:
        return not self.citations


def _cosine(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Cosine similarity of one vector against a matrix of vectors."""
    if b.size == 0:
        return np.zeros(0, dtype=np.float32)
    a_norm = a / (np.linalg.norm(a) + 1e-10)
    b_norm = b / (np.linalg.norm(b, axis=1, keepdims=True) + 1e-10)
    return b_norm @ a_norm


def mmr_select(
    query_vec: np.ndarray,
    doc_vecs: np.ndarray,
    *,
    k: int,
    lambda_mult: float,
) -> list[int]:
    """Maximal Marginal Relevance.

    Picks documents that are relevant to the query but unlike what has already
    been picked. Returns indices into `doc_vecs`, in selection order.
    """
    if doc_vecs.size == 0:
        return []

    relevance = _cosine(query_vec, doc_vecs)
    k = min(k, doc_vecs.shape[0])

    selected: list[int] = [int(np.argmax(relevance))]
    while len(selected) < k:
        best_idx, best_score = -1, -np.inf
        chosen = doc_vecs[selected]
        for i in range(doc_vecs.shape[0]):
            if i in selected:
                continue
            redundancy = float(np.max(_cosine(doc_vecs[i], chosen)))
            score = lambda_mult * float(relevance[i]) - (1.0 - lambda_mult) * redundancy
            if score > best_score:
                best_idx, best_score = i, score
        if best_idx < 0:
            break
        selected.append(best_idx)

    return selected


class RetrievalService:
    """Owns the embedding model and the Chroma collection.

    Both are shared with the ingestion service — embedding the corpus and
    embedding a query must use the identical model, or the vectors are not
    comparable and retrieval silently returns nonsense.
    """

    def __init__(self) -> None:
        self._embedder: Any | None = None
        self._client: Any | None = None
        self._collection: Any | None = None
        self._lock = asyncio.Lock()

    # ---- lazy resources ------------------------------------------------

    async def embedder(self) -> Any:
        if self._embedder is not None:
            return self._embedder
        async with self._lock:
            if self._embedder is not None:
                return self._embedder
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise DependencyMissingError(
                    "sentence-transformers", "The knowledge base"
                ) from exc

            log.info("loading embedder", extra={"model": settings.embedding_model})
            self._embedder = await asyncio.to_thread(
                SentenceTransformer, settings.embedding_model, device="cpu"
            )
            return self._embedder

    async def collection(self) -> Any:
        if self._collection is not None:
            return self._collection
        async with self._lock:
            if self._collection is not None:
                return self._collection
            try:
                import chromadb
                from chromadb.config import Settings as ChromaSettings
            except ImportError as exc:
                raise DependencyMissingError("chromadb", "The knowledge base") from exc

            settings.ensure_dirs()
            self._client = await asyncio.to_thread(
                chromadb.PersistentClient,
                path=str(settings.chroma_dir),
                settings=ChromaSettings(anonymized_telemetry=False, allow_reset=True),
            )
            self._collection = await asyncio.to_thread(
                self._client.get_or_create_collection,
                name=settings.collection_name,
                metadata={"hnsw:space": "cosine"},
            )
            return self._collection

    async def embed(self, texts: list[str], *, is_query: bool = False) -> np.ndarray:
        """Embed text.

        bge models are trained with an instruction prefix on the query side
        only; omitting it measurably degrades retrieval, and applying it to
        documents does the same.
        """
        model = await self.embedder()
        prepared = texts
        if is_query and "bge" in settings.embedding_model.lower():
            prepared = [
                f"Represent this sentence for searching relevant passages: {t}"
                for t in texts
            ]

        vectors = await asyncio.to_thread(
            model.encode,
            prepared,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return np.asarray(vectors, dtype=np.float32)

    # ---- queries -------------------------------------------------------

    async def count(self) -> int:
        try:
            collection = await self.collection()
            return int(await asyncio.to_thread(collection.count))
        except Exception:
            return 0

    async def retrieve(
        self,
        query: str,
        *,
        k: int | None = None,
        require_corpus: bool = False,
    ) -> RetrievalResult:
        k = k or settings.retrieval_k
        collection = await self.collection()

        total = int(await asyncio.to_thread(collection.count))
        if total == 0:
            if require_corpus:
                raise CorpusEmptyError()
            return RetrievalResult(citations=[], grounded=False)

        query_vec = (await self.embed([query], is_query=True))[0]
        fetch_k = min(settings.retrieval_fetch_k, total)

        raw = await asyncio.to_thread(
            collection.query,
            query_embeddings=[query_vec.tolist()],
            n_results=fetch_k,
            include=["documents", "metadatas", "distances", "embeddings"],
        )

        documents = raw.get("documents", [[]])[0] or []
        metadatas = raw.get("metadatas", [[]])[0] or []
        distances = raw.get("distances", [[]])[0] or []
        embeddings = raw.get("embeddings", [[]])[0] or []

        if not documents:
            return RetrievalResult(citations=[], grounded=False, candidates=0)

        # Chroma reports cosine *distance*; similarity is 1 - distance.
        scores = [1.0 - float(d) for d in distances]
        best = max(scores) if scores else 0.0

        if best < settings.retrieval_min_score:
            log.info(
                "retrieval below threshold",
                extra={"best": round(best, 4), "min": settings.retrieval_min_score},
            )
            return RetrievalResult(
                citations=[], grounded=False, best_score=best, candidates=len(documents)
            )

        doc_vecs = np.asarray(embeddings, dtype=np.float32)
        order = (
            mmr_select(
                query_vec, doc_vecs, k=k, lambda_mult=settings.retrieval_lambda
            )
            if doc_vecs.size
            else list(range(min(k, len(documents))))
        )

        citations: list[Citation] = []
        for idx in order:
            meta = metadatas[idx] or {}
            citations.append(
                Citation(
                    source=str(meta.get("source", "unknown")),
                    title=meta.get("title") or None,
                    chunk_index=(
                        int(meta["chunk_index"]) if "chunk_index" in meta else None
                    ),
                    score=round(scores[idx], 4),
                    excerpt=documents[idx],
                )
            )

        return RetrievalResult(
            citations=citations,
            grounded=True,
            best_score=best,
            candidates=len(documents),
        )

    async def reset(self) -> None:
        """Drop the collection. Used when re-ingesting from scratch."""
        collection = await self.collection()
        client = self._client
        if client is None:
            return
        name = collection.name
        await asyncio.to_thread(client.delete_collection, name)
        self._collection = await asyncio.to_thread(
            client.get_or_create_collection,
            name=name,
            metadata={"hnsw:space": "cosine"},
        )


retrieval_service = RetrievalService()
