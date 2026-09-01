"""Conversation history (A13) and the progress dashboard's data (A17).

Everything here is framed as practice trends. There is no score, no severity,
and no assessment — a fluency-load average is reported because it shows change
over time, not because it grades anyone (docs/ETHICS.md).
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.db.models import Session as SessionRow
from app.db.models import Turn as TurnRow
from app.db.session import get_db
from app.schemas.acoustic import AcousticProfile
from app.schemas.chat import (
    Citation,
    ProgressOut,
    ProgressPoint,
    Role,
    SessionDetail,
    SessionMetrics,
    SessionOut,
    TurnOut,
)

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


def _to_turn(row: TurnRow) -> TurnOut:
    return TurnOut(
        id=row.id,
        role=Role(row.role),
        mode=row.mode,  # type: ignore[arg-type]
        text=row.text,
        created_at=row.created_at,
        citations=[Citation(**c) for c in (row.citations or [])],
        acoustic=AcousticProfile(**row.acoustic) if row.acoustic else None,
        total_ms=row.total_ms,
    )


@router.post("", response_model=SessionOut, status_code=201)
async def create_session(db: AsyncSession = Depends(get_db)) -> SessionOut:
    row = SessionRow()
    db.add(row)
    await db.commit()
    return SessionOut(id=row.id, started_at=row.started_at, turn_count=0)


@router.get("", response_model=list[SessionOut])
async def list_sessions(
    limit: int = Query(default=30, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> list[SessionOut]:
    counts = (
        select(TurnRow.session_id, func.count(TurnRow.id).label("n"))
        .group_by(TurnRow.session_id)
        .subquery()
    )
    stmt = (
        select(SessionRow, func.coalesce(counts.c.n, 0))
        .outerjoin(counts, counts.c.session_id == SessionRow.id)
        .order_by(SessionRow.started_at.desc())
        .limit(limit)
    )
    return [
        SessionOut(
            id=row.id,
            started_at=row.started_at,
            ended_at=row.ended_at,
            title=row.title,
            turn_count=int(n),
        )
        for row, n in (await db.execute(stmt)).all()
    ]


@router.get("/progress", response_model=ProgressOut)
async def progress(
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> ProgressOut:
    """Trend across recent sessions.

    Registered before `/{session_id}` so the literal path is not captured by
    the parameterised route.
    """
    stmt = select(SessionRow).order_by(SessionRow.started_at.desc()).limit(limit)
    sessions = list((await db.execute(stmt)).scalars())

    points: list[ProgressPoint] = []
    total_ms = 0

    for s in sessions:
        metrics = await _metrics_for(db, s)
        total_ms += metrics.total_speech_ms
        if metrics.spoken_turns:
            points.append(
                ProgressPoint(
                    session_id=s.id,
                    started_at=s.started_at,
                    mean_fluency_load=metrics.mean_fluency_load,
                    mean_speech_rate_wpm=metrics.mean_speech_rate_wpm,
                    spoken_turns=metrics.spoken_turns,
                )
            )

    points.reverse()  # oldest first, so the chart reads left to right
    return ProgressOut(
        points=points, sessions=len(sessions), total_practice_ms=total_ms
    )


@router.get("/{session_id}", response_model=SessionDetail)
async def get_session(
    session_id: str, db: AsyncSession = Depends(get_db)
) -> SessionDetail:
    row = await db.get(SessionRow, session_id)
    if row is None:
        raise NotFoundError("That practice session doesn't exist.")

    turns = [_to_turn(t) for t in row.turns]
    return SessionDetail(
        id=row.id,
        started_at=row.started_at,
        ended_at=row.ended_at,
        title=row.title,
        turn_count=len(turns),
        turns=turns,
    )


@router.get("/{session_id}/metrics", response_model=SessionMetrics)
async def session_metrics(
    session_id: str, db: AsyncSession = Depends(get_db)
) -> SessionMetrics:
    row = await db.get(SessionRow, session_id)
    if row is None:
        raise NotFoundError("That practice session doesn't exist.")
    return await _metrics_for(db, row)


@router.post("/{session_id}/end", response_model=SessionOut)
async def end_session(
    session_id: str, db: AsyncSession = Depends(get_db)
) -> SessionOut:
    row = await db.get(SessionRow, session_id)
    if row is None:
        raise NotFoundError("That practice session doesn't exist.")
    if row.ended_at is None:
        row.ended_at = datetime.now(timezone.utc)
        await db.commit()
    return SessionOut(
        id=row.id,
        started_at=row.started_at,
        ended_at=row.ended_at,
        title=row.title,
        turn_count=len(row.turns),
    )


@router.delete("/{session_id}", status_code=204)
async def delete_session(session_id: str, db: AsyncSession = Depends(get_db)) -> None:
    row = await db.get(SessionRow, session_id)
    if row is None:
        raise NotFoundError("That practice session doesn't exist.")
    await db.execute(delete(SessionRow).where(SessionRow.id == session_id))
    await db.commit()


async def _metrics_for(db: AsyncSession, session: SessionRow) -> SessionMetrics:
    stmt = select(TurnRow).where(
        TurnRow.session_id == session.id, TurnRow.role == Role.USER.value
    )
    rows = list((await db.execute(stmt)).scalars())

    loads: list[float] = []
    rates: list[float] = []
    counts: dict[str, int] = {}
    total_speech = 0

    for row in rows:
        if not row.acoustic:
            continue
        profile = AcousticProfile(**row.acoustic)
        if not profile.analyzed:
            continue
        total_speech += profile.duration_ms
        loads.append(profile.fluency_load)
        if (wpm := profile.prosody.speech_rate_wpm) is not None:
            rates.append(wpm)
        for kind, n in profile.event_counts.items():
            counts[kind] = counts.get(kind, 0) + n

    return SessionMetrics(
        session_id=session.id,
        started_at=session.started_at,
        spoken_turns=len(loads),
        total_speech_ms=total_speech,
        mean_fluency_load=round(sum(loads) / len(loads), 4) if loads else 0.0,
        mean_speech_rate_wpm=round(sum(rates) / len(rates), 1) if rates else None,
        event_counts=counts,
    )
