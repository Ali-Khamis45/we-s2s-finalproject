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
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return uuid.uuid4().hex[:16]


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


class User(Base):
    """An account.

    Nothing here is clinical. `display_name` is what the interface greets
    someone by; there is no role column, because there is no admin view and no
    clinician view — a session belongs to exactly one person and nobody else
    can read it (docs/ETHICS.md, and §3 of the auth brief).
    """

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    #: Stored lowercased so uniqueness is case-insensitive without CITEXT,
    #: which SQLite does not have.
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(80))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Designed for, deliberately not enforced in a local demo.
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("0"))

    #: Lockout state. Never permanent — a permanent lock is a denial of service
    #: anyone can trigger against a known address.
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    memory_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("1"))

    sessions: Mapped[list["Session"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class RefreshToken(Base):
    """One issued refresh token.

    Only the SHA-256 is stored: a database dump must not yield usable
    credentials. `family_id` links every token descended from a single login,
    so that presenting an already-rotated token — the signature of theft — can
    revoke the whole lineage rather than just the one leaf.
    """

    __tablename__ = "refresh_tokens"
    __table_args__ = (
        Index("ix_refresh_user", "user_id"),
        Index("ix_refresh_family", "family_id"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    family_id: Mapped[str] = mapped_column(String(32), nullable=False)

    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: Set on rotation. A second presentation of a used token is a reuse attack.
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    user_agent: Mapped[str | None] = mapped_column(String(255))


class WsTicket(Base):
    """A 30-second, single-use ticket for opening a WebSocket.

    Browsers cannot set an Authorization header on a WebSocket, and putting a
    JWT in the query string writes a live credential into every access log and
    into browser history. A ticket that dies in 30 seconds and cannot be
    replayed is the cheap fix.
    """

    __tablename__ = "ws_tickets"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    #: Every session has exactly one owner, resolved from the verified token and
    #: never from anything the client sent.
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
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
    user: Mapped["User"] = relationship(back_populates="sessions")


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
