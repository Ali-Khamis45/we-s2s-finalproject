"""Knowledge base administration (A9).

Ingestion is a slow, blocking operation on CPU — embedding a few hundred chunks
takes tens of seconds — so it is an explicit endpoint rather than something that
happens on startup.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from app.api.deps import current_user
from app.db.models import User

from app.core.config import settings
from app.schemas.chat import Citation
from app.services.ingestion import ingestion_service
from app.services.retrieval import retrieval_service

router = APIRouter(prefix="/api/corpus", tags=["corpus"])


@router.get("")
async def corpus_status(user: User = Depends(current_user)) -> dict[str, Any]:
    return {
        "chunks": await retrieval_service.count(),
        "corpus_dir": str(settings.corpus_dir),
        "embedding_model": settings.embedding_model,
        "chunk_size": settings.chunk_size,
        "chunk_overlap": settings.chunk_overlap,
    }


@router.get("/manifest")
async def manifest(user: User = Depends(current_user)) -> dict[str, Any]:
    """What is indexed, by source.

    Reconcile this against data/corpus/SOURCES.md before writing the report: a
    document indexed but unlogged has no provenance, and one logged but
    unindexed is backing no answer.
    """
    return await ingestion_service.manifest()


@router.post("/ingest")
async def ingest(
    reset: bool = Query(
        default=False,
        description="Drop the existing index first. Use after removing documents.",
    ),
    user: User = Depends(current_user),
) -> dict[str, Any]:
    report = await ingestion_service.ingest_directory(reset=reset)
    return {"ok": True, **report.as_dict(), "total_chunks": await retrieval_service.count()}


@router.get("/search", response_model=list[Citation])
async def search(
    q: str = Query(..., min_length=2),
    k: int = Query(default=4, ge=1, le=20),
    user: User = Depends(current_user),
) -> list[Citation]:
    """Query retrieval directly.

    The fastest way to check whether a poor answer came from bad retrieval or
    bad generation, which is otherwise hard to tell apart.
    """
    result = await retrieval_service.retrieve(q, k=k)
    return result.citations

