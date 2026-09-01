"""Live Coach socket (A3) and the Inner Monologue consumer (A4).

Full-duplex: the browser streams microphone PCM in while Moshi's audio streams
back out, both continuously. There is no push-to-talk and no turn protocol,
which is what makes barge-in work — you can talk over the coach and it responds,
the way a person would.

Two text sources, and it matters which is which:

  Moshi's Inner Monologue is Moshi's *own* speech, predicted as text tokens
  before the audio tokens. It is what the coach is about to say, so it becomes
  the coach's side of the conversation history.

  The user's words come from Whisper, run over a tee of the same microphone
  audio. Moshi does not hand back a transcript of the user, and the acoustic
  analyzer needs that audio anyway — so the tee serves both, and it is what
  decides whether a turn needs a grounded, cited answer the live path cannot
  give (the Knowledge Mode handoff).

The tee runs off the critical path. Nothing in it can slow the ~200 ms loop:
analysis happens between utterances, and if it falls behind, it is dropped.

Client protocol
  in   binary  PCM16 mono @ SCC_MOSHI_SAMPLE_RATE
       json    {"type": "stop"}
  out  binary  PCM16 mono, coach audio
       json    mode | transcript | acoustic | handoff | error
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

import numpy as np
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from app.api.ws_auth import WS_UNAUTHORIZED, user_from_ticket
from app.core.config import settings
from app.core.logging import get_logger, set_session_id
from app.db.models import Session as SessionRow
from app.db.session import db_session
from app.schemas.chat import Mode, Role
from app.services.audio import StreamBuffer, pcm16_to_float32
from app.services.moshi import (
    MonologueAccumulator,
    MoshiAudio,
    MoshiText,
    MoshiUnavailable,
    moshi_client,
)
from app.services.orchestrator import orchestrator
from app.services.prompts import templates
from app.services.vad import Endpointer

router = APIRouter(tags=["live"])
log = get_logger(__name__)

#: Cap on concurrent analyses so a long session cannot pile up work.
MAX_PENDING_ANALYSES = 1


async def _send_json(ws: WebSocket, type_: str, data: dict[str, Any]) -> None:
    if ws.client_state is not WebSocketState.CONNECTED:
        return
    with contextlib.suppress(Exception):
        await ws.send_text(json.dumps({"type": type_, "data": data}))


async def _send_bytes(ws: WebSocket, payload: bytes) -> None:
    if ws.client_state is not WebSocketState.CONNECTED:
        return
    with contextlib.suppress(Exception):
        await ws.send_bytes(payload)


@router.websocket("/ws/live")
async def live_socket(
    websocket: WebSocket, session_id: str | None = None, ticket: str | None = None
) -> None:
    await websocket.accept()

    async with db_session() as db:
        # Owner first, before any audio is read.
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
        status = await orchestrator.mode_status()

        if not status.live_available:
            # The flagship is down. Say so plainly and point at the path that
            # still works, rather than leaving a dead socket open.
            await _send_json(
                websocket,
                "mode",
                {
                    **status.model_dump(mode="json"),
                    "session_id": session.id,
                    "fallback": "/ws/knowledge",
                },
            )
            await websocket.close(code=1013)  # try again later
            return

        await _send_json(
            websocket,
            "mode",
            {**status.model_dump(mode="json"), "session_id": session.id},
        )

        try:
            await _run_live(websocket, session.id)
        except WebSocketDisconnect:
            log.info("live client disconnected")
        except MoshiUnavailable as exc:
            moshi_client.invalidate()
            log.warning("live session lost", extra={"reason": str(exc)})
            await _send_json(
                websocket,
                "mode",
                {
                    "mode": Mode.KNOWLEDGE.value,
                    "live_available": False,
                    "reason": "lost",
                    "detail": (
                        "The live coach dropped out. Reconnect to keep going, or "
                        "switch to the slower grounded mode."
                    ),
                    "fallback": "/ws/knowledge",
                },
            )
        finally:
            with contextlib.suppress(Exception):
                if websocket.client_state is WebSocketState.CONNECTED:
                    await websocket.close()


async def _run_live(websocket: WebSocket, session_id: str) -> None:
    """Pump audio both ways until either side hangs up."""
    rate = settings.moshi_sample_rate
    tee = StreamBuffer(rate)
    endpointer = Endpointer(sample_rate=rate)
    monologue = MonologueAccumulator()
    pending: set[asyncio.Task[None]] = set()

    async with moshi_client.session() as moshi:

        async def client_to_moshi() -> None:
            while True:
                message = await websocket.receive()

                if message["type"] == "websocket.disconnect":
                    raise WebSocketDisconnect(message.get("code", 1000))

                if (payload := message.get("bytes")) is not None:
                    await moshi.send_audio(payload)

                    # Tee for the acoustic branch. Never blocks the forward path.
                    samples = pcm16_to_float32(payload)
                    tee.add(samples)
                    if endpointer.push(samples) and len(pending) < MAX_PENDING_ANALYSES:
                        utterance = tee.take()
                        task = asyncio.create_task(
                            _analyze_utterance(websocket, session_id, utterance, rate)
                        )
                        pending.add(task)
                        task.add_done_callback(pending.discard)
                    elif endpointer.state.name == "IDLE" and len(tee) > rate * 30:
                        # Nobody is speaking and the buffer has grown: drop it so
                        # a long idle session cannot accumulate memory.
                        tee.clear()

                elif (text := message.get("text")) is not None:
                    with contextlib.suppress(json.JSONDecodeError):
                        if json.loads(text).get("type") == "stop":
                            return

        async def moshi_to_client() -> None:
            async for event in moshi.events():
                if isinstance(event, MoshiAudio):
                    await _send_bytes(websocket, event.pcm)
                    continue

                if isinstance(event, MoshiText):
                    await _send_json(
                        websocket,
                        "transcript",
                        {"role": Role.COACH.value, "text": event.text, "final": False},
                    )
                    if utterance := monologue.add(event.text):
                        await _persist_coach(session_id, utterance)
                        await _send_json(
                            websocket,
                            "transcript",
                            {
                                "role": Role.COACH.value,
                                "text": utterance,
                                "final": True,
                            },
                        )

        pumps = [
            asyncio.create_task(client_to_moshi()),
            asyncio.create_task(moshi_to_client()),
        ]
        done, unfinished = await asyncio.wait(
            pumps, return_when=asyncio.FIRST_EXCEPTION
        )

        for task in unfinished:
            task.cancel()
        await asyncio.gather(*unfinished, return_exceptions=True)

        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

        # Flush a half-finished coach utterance so history is not truncated.
        if tail := monologue.flush():
            await _persist_coach(session_id, tail)

        for task in done:
            if exc := task.exception():
                raise exc


async def _analyze_utterance(
    websocket: WebSocket, session_id: str, samples: np.ndarray, rate: int
) -> None:
    """Transcribe and analyze one user utterance, off the critical path.

    Moshi has already replied by the time this runs — it exists so the timeline
    overlay and the progress dashboard have data, and so the orchestrator can
    notice a turn that wanted a grounded answer.
    """
    if samples.size < rate * 0.3:
        return

    try:
        transcript, profile = await orchestrator.analyze_only(samples, rate)
    except Exception as exc:
        log.warning("live analysis failed", extra={"reason": str(exc)})
        return

    if transcript.is_empty:
        return

    async with db_session() as db:
        session = await db.get(SessionRow, session_id)
        if session is None:
            return
        await orchestrator.record_turn(
            db,
            session=session,
            role=Role.USER,
            mode=Mode.LIVE,
            text=transcript.text,
            acoustic=profile,
        )
        await db.commit()

    await _send_json(
        websocket,
        "transcript",
        {"role": Role.USER.value, "text": transcript.text, "final": True},
    )
    await _send_json(websocket, "acoustic", profile.model_dump(mode="json"))

    # The handoff. Moshi cannot retrieve, so a question wanting reference
    # material is flagged for the client to answer over /ws/knowledge.
    if templates.wants_knowledge(transcript.text):
        await _send_json(
            websocket,
            "handoff",
            {
                "reason": "knowledge_request",
                "query": transcript.text,
                "endpoint": "/ws/knowledge",
                "detail": "That needs the reference library, which takes a moment.",
            },
        )


async def _persist_coach(session_id: str, text: str) -> None:
    async with db_session() as db:
        session = await db.get(SessionRow, session_id)
        if session is None:
            return
        await orchestrator.record_turn(
            db, session=session, role=Role.COACH, mode=Mode.LIVE, text=text
        )
        await db.commit()
