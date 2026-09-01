"""Conversational HTTP endpoints (A6).

The text path exists for three reasons: the brief requires text interaction, it
is the debuggable surface where the RAG and prompt layers can be exercised
without microphone variables in the loop, and it is what M9's evaluation harness
drives when comparing base against fine-tuned.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_user
from app.core.errors import ValidationError
from app.core.logging import set_session_id
from app.db.models import User
from app.db.session import get_db
from app.schemas.chat import ChatRequest, ChatResponse, Mode
from app.services.audio import read_wav
from app.services.orchestrator import orchestrator

router = APIRouter(prefix="/api", tags=["chat"])


@router.post("/chat", response_model=ChatResponse)
async def chat(
    payload: ChatRequest,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> ChatResponse:
    """One typed turn through the cascade."""
    session = await orchestrator.get_or_create_session(db, payload.session_id, user.id)
    set_session_id(session.id)

    result = await orchestrator.answer_text(
        db,
        session=session,
        user_text=payload.message,
        mode=Mode.TEXT,
        skip_retrieval=payload.skip_retrieval,
        llm_variant=payload.llm_variant,
    )

    return ChatResponse(
        session_id=result.session_id,
        turn_id=result.turn_id,
        mode=result.mode,
        reply=result.reply,
        citations=result.citations,
        acoustic=result.acoustic,
        grounded=result.grounded,
        timings=result.timings,
        total_ms=result.total_ms,
        llm_variant=result.llm_variant,
    )


@router.post("/chat/audio", response_model=ChatResponse)
async def chat_audio(
    audio: UploadFile = File(..., description="Mono WAV"),
    session_id: str | None = Form(default=None),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> ChatResponse:
    """One spoken turn, uploaded rather than streamed.

    The streaming socket is the real interface; this is the one that is easy to
    drive from a script, which makes it what the evaluation harness and the
    acoustic-analyzer tests use.
    """
    raw = await audio.read()
    if not raw:
        raise ValidationError("The uploaded audio was empty.")

    try:
        samples, sample_rate = read_wav(raw)
    except Exception as exc:
        raise ValidationError(
            "That file couldn't be read as WAV audio. Mono 16-bit PCM works best."
        ) from exc

    session = await orchestrator.get_or_create_session(db, session_id, user.id)
    set_session_id(session.id)

    result = await orchestrator.answer_audio(
        db, session=session, samples=samples, sample_rate=sample_rate
    )
    if result is None:
        raise ValidationError("No speech was detected in that audio.")

    return ChatResponse(
        session_id=result.session_id,
        turn_id=result.turn_id,
        mode=result.mode,
        reply=result.reply,
        citations=result.citations,
        acoustic=result.acoustic,
        grounded=result.grounded,
        timings=result.timings,
        total_ms=result.total_ms,
        llm_variant=result.llm_variant,
    )


@router.post("/analyze", tags=["acoustic"])
async def analyze(
    audio: UploadFile = File(...), user: User = Depends(current_user)
) -> dict[str, object]:
    """Transcribe and analyze without generating a reply.

    This is the endpoint that makes the project's central claim inspectable
    side by side: `transcript` is what a conventional pipeline would have kept,
    `acoustic` is everything it would have thrown away.
    """
    raw = await audio.read()
    if not raw:
        raise ValidationError("The uploaded audio was empty.")

    try:
        samples, sample_rate = read_wav(raw)
    except Exception as exc:
        raise ValidationError("That file couldn't be read as WAV audio.") from exc

    transcript, profile = await orchestrator.analyze_only(samples, sample_rate)
    return {
        "transcript": transcript.text,
        "words": [
            {"text": w.text, "start_ms": w.start_ms, "end_ms": w.end_ms}
            for w in transcript.words
        ],
        "acoustic": profile.model_dump(mode="json"),
    }

