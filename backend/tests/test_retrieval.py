"""Retrieval failure modes.

The corpus has one safety property that matters: it refuses to answer outside
its own material. That guarantee rests on `corpus_chunks` being *true*. An
index that cannot be read reports the same `0` as an index with nothing in it,
so the app degrades to "ungrounded but confident" -- the one outcome the gate
exists to prevent. These tests pin the difference.
"""

from __future__ import annotations

import pytest

from app.core.errors import CorpusUnreadableError
from app.services.retrieval import RetrievalService


class _UnreadableCollection:
    """Stands in for a Chroma collection written by a newer Chroma.

    The real failure is `KeyError: '_type'` raised out of
    `chromadb/api/configuration.py` when 0.5.23 reads a newer collection
    configuration. What matters is that it is an arbitrary exception from
    inside the driver, not a typed one this code could have anticipated.
    """

    def count(self) -> int:
        raise KeyError("_type")


class _EmptyCollection:
    def count(self) -> int:
        return 0


@pytest.mark.asyncio
class TestCountDistinguishesUnreadableFromEmpty:
    async def test_empty_index_counts_zero(self):
        service = RetrievalService()
        service._collection = _EmptyCollection()

        assert await service.count() == 0

    async def test_unreadable_index_raises_instead_of_reporting_zero(self):
        service = RetrievalService()
        service._collection = _UnreadableCollection()

        with pytest.raises(CorpusUnreadableError):
            await service.count()

    async def test_unreadable_index_names_the_driver_error(self):
        """The operator needs the underlying cause, not just 'it broke'."""
        service = RetrievalService()
        service._collection = _UnreadableCollection()

        with pytest.raises(CorpusUnreadableError) as caught:
            await service.count()

        assert "KeyError" in caught.value.detail.get("cause", "")


@pytest.mark.asyncio
class TestStatusSurvivesAnUnreadableCorpus:
    """`/api/status` must report the breakage, not become it.

    The dashboard is how an operator finds out something is wrong, so it has to
    keep answering when the corpus cannot be read. It reports the corpus as
    unhealthy rather than propagating a 503 and taking the whole panel down.
    """

    async def test_health_reports_unreadable_without_raising(self, monkeypatch):
        from app.services.orchestrator import orchestrator
        from app.services.retrieval import retrieval_service

        monkeypatch.setattr(retrieval_service, "_collection", _UnreadableCollection())

        health = await orchestrator.health()

        assert health["corpus_chunks"] == 0
        assert health["corpus_status"] == "unreadable"

    async def test_health_reports_ok_for_a_readable_corpus(self, monkeypatch):
        from app.services.orchestrator import orchestrator
        from app.services.retrieval import retrieval_service

        monkeypatch.setattr(retrieval_service, "_collection", _EmptyCollection())

        health = await orchestrator.health()

        assert health["corpus_chunks"] == 0
        assert health["corpus_status"] == "ok"
