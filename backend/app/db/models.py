"""Persistence for conversation history (A13).

History is unified across both modes: a session can start in Live Coach, hand
off to Knowledge Mode for a grounded answer, and come back, and the turns land
in one ordered thread. That is what makes the mode switch invisible to the user
and what lets the progress dashboard (A17) aggregate a whole session.

Audio is not stored. Turns keep the transcript and the derived acoustic profile;
raw waveforms are discarded once analyzed (docs/ETHICS.md).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return uuid.uuid4().hex[:16]


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Derived from the first user turn so the history list is scannable.
    title: Mapped[str | None] = mapped_column(String(120))

    turns: Mapped[list["Turn"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="Turn.id",
        lazy="selectin",
    )


class Turn(Base):
    __tablename__ = "turns"
    __table_args__ = (Index("ix_turns_session_id", "session_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )

    role: Mapped[str] = mapped_column(String(16), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    text: Mapped[str] = mapped_column(Text, default="")

    #: AcousticProfile, on user turns that carried audio. Null for typed input.
    acoustic: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    #: Citation list, on coach turns produced by the cascade.
    citations: Mapped[list[Any] | None] = mapped_column(JSON)
    #: Per-stage timings, kept per turn so M10/M12 can compute p50/p95 from real
    #: sessions instead of a synthetic benchmark loop.
    timings: Mapped[list[Any] | None] = mapped_column(JSON)
    total_ms: Mapped[float | None] = mapped_column(Float)
    llm_variant: Mapped[str | None] = mapped_column(String(32))

    session: Mapped[Session] = relationship(back_populates="turns")
