"""Request and response shapes for the conversational endpoints."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.acoustic import AcousticProfile


class Mode(StrEnum):
    """Which path produced a turn."""

    LIVE = "live"            # native S2S, Moshi
    KNOWLEDGE = "knowledge"  # cascade with retrieval
    TEXT = "text"            # cascade, typed input


class Role(StrEnum):
    USER = "user"
    COACH = "coach"


class Citation(BaseModel):
    """One retrieved chunk backing a claim in the reply."""

    source: str
    title: str | None = None
    chunk_index: int | None = None
    score: float | None = None
    excerpt: str = ""


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4_000)
    session_id: str | None = None
    #: Force the cascade to skip retrieval — used by the M9 evaluation harness
    #: so base and fine-tuned checkpoints are compared on identical prompts.
    skip_retrieval: bool = False
    llm_variant: str | None = None


class StageTiming(BaseModel):
    """Per-stage latency, collected for M10 and M12."""

    stage: str
    ms: float


class ChatResponse(BaseModel):
    session_id: str
    turn_id: int
    mode: Mode
    reply: str
    citations: list[Citation] = Field(default_factory=list)
    acoustic: AcousticProfile | None = None
    grounded: bool = True
    timings: list[StageTiming] = Field(default_factory=list)
    total_ms: float = 0.0
    llm_variant: str = "base"


class TurnOut(BaseModel):
    id: int
    role: Role
    mode: Mode
    text: str
    created_at: datetime
    citations: list[Citation] = Field(default_factory=list)
    acoustic: AcousticProfile | None = None
    total_ms: float | None = None

    model_config = {"from_attributes": True}


class SessionOut(BaseModel):
    id: str
    started_at: datetime
    ended_at: datetime | None = None
    turn_count: int = 0
    title: str | None = None

    model_config = {"from_attributes": True}


class SessionDetail(SessionOut):
    turns: list[TurnOut] = Field(default_factory=list)


class SessionMetrics(BaseModel):
    """Aggregates behind the progress dashboard (A17).

    Framed as practice trends, never as an assessment (docs/ETHICS.md).
    """

    session_id: str
    started_at: datetime
    spoken_turns: int = 0
    total_speech_ms: int = 0
    mean_fluency_load: float = 0.0
    mean_speech_rate_wpm: float | None = None
    event_counts: dict[str, int] = Field(default_factory=dict)


class ProgressPoint(BaseModel):
    session_id: str
    started_at: datetime
    mean_fluency_load: float
    mean_speech_rate_wpm: float | None = None
    spoken_turns: int


class ProgressOut(BaseModel):
    points: list[ProgressPoint] = Field(default_factory=list)
    sessions: int = 0
    total_practice_ms: int = 0


# ---- WebSocket frames -------------------------------------------------
#
# One envelope for both sockets. Binary frames carry raw PCM; JSON frames carry
# everything else, discriminated on `type`.


class WSFrame(BaseModel):
    type: str
    data: dict[str, Any] = Field(default_factory=dict)


class ModeStatus(BaseModel):
    """Pushed whenever the active path changes.

    The UI shows this: a user needs to know they dropped from the ~200 ms live
    coach to the ~1 s cascade, and why.
    """

    mode: Mode
    live_available: bool
    reason: str | None = None
    detail: str | None = None


class TranscriptDelta(BaseModel):
    """Incremental text.

    In Live mode this is Moshi's Inner Monologue (A4); in Knowledge mode it is
    Whisper's partial transcript or the LLM's streamed reply.
    """

    role: Role
    text: str
    final: bool = False


class AudioMeta(BaseModel):
    """Describes the binary frames that follow."""

    sample_rate: int
    channels: int = 1
    format: Literal["pcm_s16le", "pcm_f32le"] = "pcm_s16le"
    speech_rate: float = 1.0
