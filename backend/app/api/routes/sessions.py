"""Conversation history (A13) and the progress dashboard's data (A17).

Every query here is owner-scoped. There is no route that can return another
account's turns, and no aggregate that mixes two people's speech — `/progress`
computing across all rows would silently average a stranger's pacing into
somebody's chart.

Everything is framed as practice trends. There is no score, no severity, and no
assessment (docs/ETHICS.md).
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_user, owned_session
from app.db.models import Session as SessionRow
from app.db.models import Turn as TurnRow
from app.db.models import User
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


class RenameRequest(BaseModel):
    title: str = Field(min_length=1, max_length=120)


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
async def create_session(
    user: User = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> SessionOut:
    # The owner comes from the verified token, never from the request body.
    row = SessionRow(user_id=user.id)
    db.add(row)
    await db.commit()
    return SessionOut(id=row.id, started_at=row.started_at, turn_count=0)


@router.get("", response_model=list[SessionOut])
async def list_sessions(
    limit: int = Query(default=50, ge=1, le=200),
    q: str | None = Query(default=None, max_length=120),
    user: User = Depends(current_user),
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
        .where(SessionRow.user_id == user.id)
        .order_by(SessionRow.started_at.desc())
        .limit(limit)
    )
    if q:
        stmt = stmt.where(SessionRow.title.ilike(f"%{q}%"))

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
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> ProgressOut:
    """Trend across this account's recent sessions.

    Registered before `/{session_id}` so the literal path is not captured by
    the parameterised route.
    """
    stmt = (
        select(SessionRow)
        .where(SessionRow.user_id == user.id)
        .order_by(SessionRow.started_at.desc())
        .limit(limit)
    )
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
    return ProgressOut(points=points, sessions=len(sessions), total_practice_ms=total_ms)


@router.get("/{session_id}", response_model=SessionDetail)
async def get_session(row: SessionRow = Depends(owned_session)) -> SessionDetail:
    turns = [_to_turn(t) for t in row.turns]
    return SessionDetail(
        id=row.id,
        started_at=row.started_at,
        ended_at=row.ended_at,
        title=row.title,
        turn_count=len(turns),
        turns=turns,
    )


@router.patch("/{session_id}", response_model=SessionOut)
async def rename_session(
    payload: RenameRequest,
    row: SessionRow = Depends(owned_session),
    db: AsyncSession = Depends(get_db),
) -> SessionOut:
    row.title = payload.title.strip()
    await db.commit()
    return SessionOut(
        id=row.id,
        started_at=row.started_at,
        ended_at=row.ended_at,
        title=row.title,
        turn_count=len(row.turns),
    )


@router.get("/{session_id}/metrics", response_model=SessionMetrics)
async def session_metrics(
    row: SessionRow = Depends(owned_session), db: AsyncSession = Depends(get_db)
) -> SessionMetrics:
    return await _metrics_for(db, row)


@router.post("/{session_id}/end", response_model=SessionOut)
async def end_session(
    row: SessionRow = Depends(owned_session), db: AsyncSession = Depends(get_db)
) -> SessionOut:
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
async def delete_session(
    row: SessionRow = Depends(owned_session), db: AsyncSession = Depends(get_db)
) -> None:
    await db.execute(delete(TurnRow).where(TurnRow.session_id == row.id))
    await db.execute(delete(SessionRow).where(SessionRow.id == row.id))
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
