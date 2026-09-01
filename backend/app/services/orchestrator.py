"""Mode orchestrator (A14).

The seam where the two paths meet. Everything the API layer needs is here, so
the HTTP routes and the WebSocket handlers share one implementation of a turn
rather than each growing their own.

Three responsibilities:

  routing       Decide whether a turn is answered live or needs the grounded
                cascade, and record which path ran.
  degradation   When Moshi is unreachable the session continues on the cascade
                and says so. A dead flagship must never mean a dead product —
                this is the mitigation for the project's largest risk.
  history       One ordered thread across both modes, so a session that hands
                off mid-conversation still reads as one conversation and the
                progress dashboard can aggregate it.

Every turn is timed per stage. Those timings are persisted, not just logged,
which is what lets M10 and M12 compute p50/p95 from real sessions instead of a
synthetic benchmark loop.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import NotFoundError
from app.core.logging import get_logger
from app.db.models import Session as SessionRow
from app.db.models import Turn as TurnRow
from app.schemas.acoustic import AcousticProfile
from app.schemas.chat import Citation, Mode, ModeStatus, Role, StageTiming
from app.services.dysfluency import dysfluency_analyzer
from app.services.llm import llm_service
from app.services.moshi import moshi_client
from app.services.prompts import templates
from app.services.retrieval import retrieval_service
from app.services.stt import Transcript, stt_service

log = get_logger(__name__)


@dataclass(slots=True)
class Timer:
    """Collects per-stage durations for one turn."""

    started: float = field(default_factory=time.perf_counter)
    stages: list[StageTiming] = field(default_factory=list)

    def mark(self, name: str, started_at: float) -> float:
        ms = round((time.perf_counter() - started_at) * 1000, 1)
        self.stages.append(StageTiming(stage=name, ms=ms))
        return ms

    @property
    def total_ms(self) -> float:
        return round((time.perf_counter() - self.started) * 1000, 1)


@dataclass(slots=True)
class TurnResult:
    session_id: str
    turn_id: int
    mode: Mode
    reply: str
    citations: list[Citation] = field(default_factory=list)
    acoustic: AcousticProfile | None = None
    grounded: bool = True
    timings: list[StageTiming] = field(default_factory=list)
    total_ms: float = 0.0
    llm_variant: str = "base"
    transcript: str = ""


class Orchestrator:
    # ---- sessions & history --------------------------------------------

    async def get_or_create_session(
        self, db: AsyncSession, session_id: str | None, user_id: str
    ) -> SessionRow:
        """Resolve a session for this owner, or start one.

        `user_id` always comes from a verified token. A session id that belongs
        to somebody else is reported as absent, not forbidden — a 403 would
        confirm the id is real.
        """
        if session_id:
            row = await db.get(SessionRow, session_id)
            if row is None or row.user_id != user_id:
                raise NotFoundError("That practice session doesn't exist.")
            return row
        row = SessionRow(user_id=user_id)
        db.add(row)
        await db.flush()
        return row

    async def history_for(
        self, db: AsyncSession, session_id: str
    ) -> list[templates.HistoryTurn]:
        """Recent turns, oldest first, for the prompt's context window."""
        limit = settings.history_turns
        stmt = (
            select(TurnRow)
            .where(TurnRow.session_id == session_id)
            .order_by(TurnRow.id.desc())
            .limit(limit)
        )
        rows = list((await db.execute(stmt)).scalars())
        rows.reverse()
        return [
            templates.HistoryTurn(role=Role(r.role), text=r.text)
            for r in rows
            if r.text.strip()
        ]

    async def record_turn(
        self,
        db: AsyncSession,
        *,
        session: SessionRow,
        role: Role,
        mode: Mode,
        text: str,
        acoustic: AcousticProfile | None = None,
        citations: list[Citation] | None = None,
        timings: list[StageTiming] | None = None,
        total_ms: float | None = None,
        llm_variant: str | None = None,
    ) -> TurnRow:
        row = TurnRow(
            session_id=session.id,
            role=role.value,
            mode=mode.value,
            text=text,
            acoustic=acoustic.model_dump(mode="json") if acoustic else None,
            citations=[c.model_dump(mode="json") for c in citations] if citations else None,
            timings=[t.model_dump() for t in timings] if timings else None,
            total_ms=total_ms,
            llm_variant=llm_variant,
        )
        db.add(row)

        if session.title is None and role is Role.USER and text.strip():
            session.title = templates.build_session_title(text)

        await db.flush()
        return row

    # ---- mode routing ---------------------------------------------------

    async def mode_status(self) -> ModeStatus:
        """What the session can currently do.

        The UI shows this: a user who drops from the ~200 ms live coach to the
        ~1 s cascade should be told, not left wondering why it got slower.
        """
        if not moshi_client.enabled:
            return ModeStatus(
                mode=Mode.KNOWLEDGE,
                live_available=False,
                reason="disabled",
                detail="The live coach is turned off in this configuration.",
            )

        if await moshi_client.available():
            return ModeStatus(mode=Mode.LIVE, live_available=True)

        return ModeStatus(
            mode=Mode.KNOWLEDGE,
            live_available=False,
            reason="unreachable",
            detail=(
                "The live coach isn't running, so responses will take about a "
                "second instead of being instant. Everything else works."
            ),
        )

    def should_retrieve(self, text: str) -> bool:
        return templates.wants_knowledge(text)

    # ---- the cascade ----------------------------------------------------

    async def answer_text(
        self,
        db: AsyncSession,
        *,
        session: SessionRow,
        user_text: str,
        acoustic: AcousticProfile | None = None,
        mode: Mode = Mode.TEXT,
        skip_retrieval: bool = False,
        llm_variant: str | None = None,
        few_shot: bool = True,
    ) -> TurnResult:
        """Run one full cascade turn and persist both sides of it."""
        timer = Timer()
        variant = llm_variant or settings.llm_variant

        await self.record_turn(
            db,
            session=session,
            role=Role.USER,
            mode=mode,
            text=user_text,
            acoustic=acoustic,
        )

        citations: list[Citation] = []
        grounded = True
        if not skip_retrieval and self.should_retrieve(user_text):
            t0 = time.perf_counter()
            try:
                result = await retrieval_service.retrieve(user_text)
                citations = result.citations
                grounded = result.grounded
            except Exception as exc:
                # Retrieval is an enhancement. Losing it degrades the answer;
                # it must not lose the conversation.
                log.warning("retrieval failed", extra={"reason": str(exc)})
                grounded = False
            timer.mark("retrieval", t0)

        history = await self.history_for(db, session.id)
        # Drop the turn just recorded — it is supplied separately, with its
        # structured context blocks attached.
        if history and history[-1].role is Role.USER:
            history = history[:-1]

        bundle = templates.build(
            user_text=user_text,
            acoustic=acoustic,
            citations=citations,
            history=history,
            few_shot=few_shot,
        )
        log.info("prompt built", extra=bundle.describe())

        t0 = time.perf_counter()
        completion = await llm_service.complete(bundle.messages, variant=variant)
        timer.mark("llm", t0)

        reply = completion.text or (
            "I didn't catch that — could you say it again?"
        )

        coach_turn = await self.record_turn(
            db,
            session=session,
            role=Role.COACH,
            mode=mode,
            text=reply,
            citations=citations,
            timings=timer.stages,
            total_ms=timer.total_ms,
            llm_variant=variant,
        )
        await db.commit()

        return TurnResult(
            session_id=session.id,
            turn_id=coach_turn.id,
            mode=mode,
            reply=reply,
            citations=citations,
            acoustic=acoustic,
            grounded=grounded,
            timings=timer.stages,
            total_ms=timer.total_ms,
            llm_variant=variant,
            transcript=user_text,
        )

    async def answer_audio(
        self,
        db: AsyncSession,
        *,
        session: SessionRow,
        samples: np.ndarray,
        sample_rate: int,
        mode: Mode = Mode.KNOWLEDGE,
        llm_variant: str | None = None,
    ) -> TurnResult | None:
        """Transcribe, analyze, then answer. Returns None on silence."""
        timer = Timer()

        t0 = time.perf_counter()
        transcript: Transcript = await stt_service.transcribe(samples, sample_rate)
        stt_ms = timer.mark("stt", t0)

        if transcript.is_empty:
            log.info("empty transcript", extra={"stt_ms": stt_ms})
            return None

        # Sequential, not concurrent: the heuristic analyzer derives blocks and
        # pauses from Whisper's word timings, so it needs the transcript. It
        # adds ~30 ms, which is not where the cascade's latency lives.
        t0 = time.perf_counter()
        acoustic = await dysfluency_analyzer.analyze(samples, sample_rate, transcript)
        timer.mark("acoustic", t0)

        result = await self.answer_text(
            db,
            session=session,
            user_text=transcript.text,
            acoustic=acoustic,
            mode=mode,
            llm_variant=llm_variant,
        )

        # Fold the audio-only stages into the reported timing, so `total_ms`
        # means time-to-reply from speech, not from text.
        result.timings = timer.stages + result.timings
        result.total_ms = round(timer.total_ms, 1)
        return result

    async def analyze_only(
        self, samples: np.ndarray, sample_rate: int
    ) -> tuple[Transcript, AcousticProfile]:
        """Transcribe and analyze without generating a reply.

        Used on the live path: Moshi answers the user directly, but the acoustic
        branch still runs so the timeline overlay and the progress dashboard
        have something to show.
        """
        transcript = await stt_service.transcribe(samples, sample_rate)
        acoustic = await dysfluency_analyzer.analyze(samples, sample_rate, transcript)
        return transcript, acoustic

    async def health(self) -> dict[str, object]:
        corpus = await retrieval_service.count()
        return {
            "live_available": await moshi_client.available(),
            "llm_reachable": await llm_service.health(),
            "stt_loaded": stt_service.loaded,
            "corpus_chunks": corpus,
            "analyzer": dysfluency_analyzer.backend_name,
            "prompt_version": templates.PROMPT_VERSION,
            "llm_variant": settings.llm_variant,
        }


orchestrator = Orchestrator()
