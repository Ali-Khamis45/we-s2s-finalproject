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


class TestDrillDetection:
    """Back-of-chapter homework is not coaching prose.

    The corpus is Edwardian textbooks, so ~8% of it is numbered exercise lists
    ("2. What are the four special effects of pause?"). Returned as a citation
    that is worse than useless -- it answers a question with homework.

    These chunks are *marked*, never dropped. Measured over the real corpus,
    25% of what a structural detector flags is majority prose that chunk
    overlap dragged a numbered tail into, and dropping chunks would move the
    corpus size that `retrieval_min_score` was calibrated against.
    """

    def test_numbered_drill_list_is_detected(self):
        from app.services.ingestion import is_drill_chunk

        drill = (
            "2. What are the four special effects of pause?\n\n"
            "3. Note the pauses in a conversation, play, or speech.\n\n"
            "4. Read aloud selections on pages 50-54.\n\n"
            "5. Read the following without making any pauses."
        )
        assert is_drill_chunk(drill) is True

    def test_ordinary_prose_is_not_a_drill(self):
        from app.services.ingestion import is_drill_chunk

        prose = (
            "The apparel oft proclaims the man; the voice always does -- it "
            "is one of the greatest revealers of character. As to health, "
            "neither scope nor space permits us to discuss the laws of hygiene."
        )
        assert is_drill_chunk(prose) is False

    def test_prose_with_one_stray_numbered_line_is_not_a_drill(self):
        """A single citation or footnote must not condemn a chunk."""
        from app.services.ingestion import is_drill_chunk

        prose = (
            "Gesture is the language of the body, and it speaks before the "
            "voice is heard.\n\n"
            "1. See the note on page 40 for a fuller treatment of this.\n\n"
            "The speaker who stands still is not therefore composed; he is "
            "merely motionless, and an audience knows the difference."
        )
        assert is_drill_chunk(prose) is False

    def test_majority_prose_ending_in_a_drill_is_not_dropped(self):
        """The overlap case: real prose carrying a numbered tail.

        This is the 25%. Flagging it would delete genuine coaching material.
        """
        from app.services.ingestion import is_drill_chunk

        mixed = (
            "Change of tempo is the most valuable of all the tools the "
            "speaker owns, and the one least often used. A singer holds the "
            "charm of his singing by a continual change of tempo. Imagine a "
            "song written with but quarter notes; imagine an auto with only "
            "one speed. The monotony is not in the material but in the "
            "handling of it, and the remedy is entirely within reach.\n\n"
            "1. Note the change of tempo in the following."
        )
        assert is_drill_chunk(mixed) is False


class TestDrillsAreDemotedNotDropped:
    """Ranking, not deletion.

    A drill chunk stays in the index and stays countable -- the gate is
    calibrated against corpus size -- but sinks below real prose when the
    reranker chooses what to cite.
    """

    def test_penalty_pushes_a_drill_below_equally_relevant_prose(self):
        import numpy as np

        from app.services.retrieval import mmr_select

        query = np.array([1.0, 0.0], dtype=np.float32)
        # Two candidates the embedder likes identically.
        docs = np.array([[1.0, 0.0], [1.0, 0.0]], dtype=np.float32)

        # Index 0 is a drill, index 1 is prose. Prose must win.
        order = mmr_select(
            query, docs, k=1, lambda_mult=0.5, penalties=[0.5, 0.0]
        )
        assert order == [1]

    def test_without_penalties_selection_is_unchanged(self):
        """The penalty argument is optional and defaults to no effect."""
        import numpy as np

        from app.services.retrieval import mmr_select

        query = np.array([1.0, 0.0], dtype=np.float32)
        docs = np.array([[1.0, 0.0], [0.2, 0.98]], dtype=np.float32)

        assert mmr_select(query, docs, k=1, lambda_mult=0.5) == [0]

    def test_a_drill_still_wins_when_nothing_else_is_relevant(self):
        """Demotion is not exclusion -- a drill is better than no citation."""
        import numpy as np

        from app.services.retrieval import mmr_select

        query = np.array([1.0, 0.0], dtype=np.float32)
        docs = np.array([[1.0, 0.0]], dtype=np.float32)

        assert mmr_select(query, docs, k=1, lambda_mult=0.5, penalties=[0.5]) == [0]
