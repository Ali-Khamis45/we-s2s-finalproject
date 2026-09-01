"""Grounded Knowledge socket (A6).

The cascade, streamed. Slower than the live path by design — it stops to
retrieve and cite — but it is the path that carries five of the brief's eleven
required features, and it is what the whole product falls back to when Moshi is
unavailable.

Latency is managed rather than eliminated. The reply is streamed from the LLM
and synthesized sentence by sentence, so audio starts playing while the rest is
still being generated. Time to first audio lands around 750 ms – 1.1 s; the
honest comparison against Moshi's ~200 ms is the project's headline result, not
something to hide.

The speaking rate of the reply comes from the acoustic profile of the utterance
that prompted it. A long block slows the coach down. That is the acoustic branch
reaching all the way to the output.

Client protocol
  in   binary  PCM16 mono @ SCC_STT_SAMPLE_RATE
       json    {"type":"text","data":{"message": "..."}} | {"type":"flush"} | {"type":"stop"}
  out  binary  PCM16 mono @ SCC_TTS_SAMPLE_RATE, coach audio
       json    ready | transcript | acoustic | citations | audio_meta | done | error
"""

from __future__ import annotations

import contextlib
import json
import time
from typing import Any

import numpy as np
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from app.api.ws_auth import WS_UNAUTHORIZED, user_from_ticket
from app.core.config import settings
from app.core.errors import AppError
from app.core.logging import get_logger, set_session_id, stage
from app.db.models import Session as SessionRow
from app.db.session import db_session
from app.schemas.acoustic import AcousticProfile
from app.schemas.chat import AudioMeta, Mode, Role, StageTiming
from app.services.audio import StreamBuffer, float32_to_pcm16, pcm16_to_float32
from app.services.llm import llm_service
from app.services.orchestrator import Timer, orchestrator
from app.services.prompts import templates
from app.services.retrieval import retrieval_service
from app.services.tts import tts_service
from app.services.vad import Endpointer

router = APIRouter(tags=["knowledge"])
log = get_logger(__name__)


async def _send_json(ws: WebSocket, type_: str, data: dict[str, Any]) -> None:
    if ws.client_state is not WebSocketState.CONNECTED:
        return
    with contextlib.suppress(Exception):
        await ws.send_text(json.dumps({"type": type_, "data": data}))


@router.websocket("/ws/knowledge")
async def knowledge_socket(
    websocket: WebSocket, session_id: str | None = None, ticket: str | None = None
) -> None:
    await websocket.accept()
    rate = settings.stt_sample_rate

    async with db_session() as db:
        # Resolve the owner before reading anything. An unauthenticated socket
        # must never reach the point of receiving a microphone frame.
        user = await user_from_ticket(db, ticket)
        if user is None:
            await websocket.close(code=WS_UNAUTHORIZED)
            return

        try:
            session = await orchestrator.get_or_create_session(db, session_id, user.id)
            await db.commit()
        except Exception as exc:
            await _send_json(websocket, "error", {"message": str(exc)})
            await websocket.close()
            return

    set_session_id(session.id)
    await _send_json(
        websocket,
        "ready",
        {
            "session_id": session.id,
            "mode": Mode.KNOWLEDGE.value,
            "input_sample_rate": rate,
            "output_sample_rate": settings.tts_sample_rate,
        },
    )

    buffer = StreamBuffer(rate)
    endpointer = Endpointer(sample_rate=rate)

    try:
        while True:
            message = await websocket.receive()

            if message["type"] == "websocket.disconnect":
                break

            if (payload := message.get("bytes")) is not None:
                samples = pcm16_to_float32(payload)
                buffer.add(samples)
                if endpointer.push(samples):
                    await _handle_utterance(websocket, session.id, buffer.take(), rate)
                continue

            if (text := message.get("text")) is None:
                continue

            try:
                frame = json.loads(text)
            except json.JSONDecodeError:
                continue

            kind = frame.get("type")

            if kind == "stop":
                break

            if kind == "flush":
                # Client-side push-to-talk release: answer what we have without
                # waiting for the silence timer.
                endpointer.reset()
                if len(buffer):
                    await _handle_utterance(websocket, session.id, buffer.take(), rate)

            elif kind == "text":
                if msg := (frame.get("data") or {}).get("message", "").strip():
                    await _handle_text(websocket, session.id, msg)

    except WebSocketDisconnect:
        log.info("knowledge client disconnected")
    except Exception as exc:
        log.exception("knowledge socket failed")
        await _send_json(websocket, "error", {"message": str(exc)})
    finally:
        with contextlib.suppress(Exception):
            if websocket.client_state is WebSocketState.CONNECTED:
                await websocket.close()


async def _handle_utterance(
    websocket: WebSocket, session_id: str, samples: np.ndarray, rate: int
) -> None:
    """Transcribe, analyze, then answer with grounded, spoken output."""
    if samples.size < rate * 0.25:
        return

    timer = Timer()

    try:
        with stage(log, "stt", samples=int(samples.size)):
            transcript, profile = await orchestrator.analyze_only(samples, rate)
    except AppError as exc:
        await _send_json(websocket, "error", {"message": exc.message, "code": exc.code})
        return

    if transcript.is_empty:
        return

    await _send_json(
        websocket,
        "transcript",
        {"role": Role.USER.value, "text": transcript.text, "final": True},
    )
    await _send_json(websocket, "acoustic", profile.model_dump(mode="json"))

    await _respond(
        websocket,
        session_id,
        user_text=transcript.text,
        acoustic=profile,
        timer=timer,
        speak=True,
    )


async def _handle_text(websocket: WebSocket, session_id: str, message: str) -> None:
    """A typed turn over the same socket, so the UI needs only one connection."""
    await _send_json(
        websocket,
        "transcript",
        {"role": Role.USER.value, "text": message, "final": True},
    )
    await _respond(
        websocket,
        session_id,
        user_text=message,
        acoustic=None,
        timer=Timer(),
        speak=False,
    )


async def _respond(
    websocket: WebSocket,
    session_id: str,
    *,
    user_text: str,
    acoustic: AcousticProfile | None,
    timer: Timer,
    speak: bool,
) -> None:
    """Retrieve, generate, speak, persist."""
    citations = []
    grounded = True

    if orchestrator.should_retrieve(user_text):
        t0 = time.perf_counter()
        try:
            result = await retrieval_service.retrieve(user_text)
            citations = result.citations
            grounded = result.grounded
        except Exception as exc:
            log.warning("retrieval failed", extra={"reason": str(exc)})
            grounded = False
        timer.mark("retrieval", t0)

        await _send_json(
            websocket,
            "citations",
            {
                "grounded": grounded,
                "citations": [c.model_dump(mode="json") for c in citations],
            },
        )

    async with db_session() as db:
        session = await db.get(SessionRow, session_id)
        if session is None:
            return

        await orchestrator.record_turn(
            db,
            session=session,
            role=Role.USER,
            mode=Mode.KNOWLEDGE,
            text=user_text,
            acoustic=acoustic,
        )

        history = await orchestrator.history_for(db, session_id)
        if history and history[-1].role is Role.USER:
            history = history[:-1]

        bundle = templates.build(
            user_text=user_text,
            acoustic=acoustic,
            citations=citations,
            history=history,
        )
        log.info("prompt built", extra=bundle.describe())

        speech_rate = tts_service.rate_for(acoustic) if speak else 1.0
        if speak:
            await _send_json(
                websocket,
                "audio_meta",
                AudioMeta(
                    sample_rate=settings.tts_sample_rate, speech_rate=speech_rate
                ).model_dump(mode="json"),
            )

        # Stream the reply and synthesize it a sentence at a time, so audio
        # starts before generation finishes.
        t0 = time.perf_counter()
        ttft_ms: float | None = None
        spoken_upto = 0
        reply_parts: list[str] = []

        try:
            async for delta in llm_service.stream(bundle.messages):
                if ttft_ms is None:
                    ttft_ms = round((time.perf_counter() - t0) * 1000, 1)
                    timer.stages.append(StageTiming(stage="llm_ttft", ms=ttft_ms))

                reply_parts.append(delta)
                await _send_json(
                    websocket,
                    "transcript",
                    {"role": Role.COACH.value, "text": delta, "final": False},
                )

                if speak:
                    ready = _next_sentence("".join(reply_parts), spoken_upto)
                    if ready is not None:
                        chunk, spoken_upto = ready
                        await _speak(websocket, chunk, speech_rate)

        except AppError as exc:
            await _send_json(
                websocket, "error", {"message": exc.message, "code": exc.code}
            )
            return

        timer.mark("llm", t0)
        reply = "".join(reply_parts).strip()

        if speak and reply[spoken_upto:].strip():
            await _speak(websocket, reply[spoken_upto:].strip(), speech_rate)

        if not reply:
            reply = "I didn't catch that — could you say it again?"

        turn = await orchestrator.record_turn(
            db,
            session=session,
            role=Role.COACH,
            mode=Mode.KNOWLEDGE,
            text=reply,
            citations=citations,
            timings=timer.stages,
            total_ms=timer.total_ms,
            llm_variant=settings.llm_variant,
        )
        await db.commit()

    await _send_json(
        websocket,
        "done",
        {
            "turn_id": turn.id,
            "reply": reply,
            "grounded": grounded,
            "speech_rate": speech_rate,
            "timings": [t.model_dump() for t in timer.stages],
            "total_ms": timer.total_ms,
        },
    )


def _next_sentence(text: str, already_spoken: int) -> tuple[str, int] | None:
    """Find a complete sentence not yet synthesized.

    Returns the sentence and the new offset, or None if the tail is still
    mid-sentence. Synthesizing on sentence boundaries keeps prosody natural —
    Kokoro needs the whole clause to place stress correctly, so splitting on
    token deltas would produce audibly choppy speech.
    """
    pending = text[already_spoken:]
    if not pending:
        return None

    cut = -1
    for i, ch in enumerate(pending):
        if ch in ".!?" and i + 1 < len(pending) and pending[i + 1] in " \n":
            cut = i + 1
    if cut < 0:
        return None

    sentence = pending[:cut].strip()
    if not sentence:
        return None
    return sentence, already_spoken + cut


async def _speak(websocket: WebSocket, text: str, rate: float) -> None:
    """Synthesize one chunk and push it as binary frames."""
    try:
        async for audio, _ in tts_service.stream(text, speed=rate):
            if websocket.client_state is not WebSocketState.CONNECTED:
                return
            await websocket.send_bytes(float32_to_pcm16(audio))
    except AppError as exc:
        # TTS is recommended, not required. Losing the voice must not lose the
        # reply — the text has already been streamed to the client.
        log.warning("tts unavailable", extra={"reason": exc.message})
        await _send_json(websocket, "error", {"message": exc.message, "code": exc.code})
    except Exception as exc:
        log.warning("tts failed", extra={"reason": str(exc)})
