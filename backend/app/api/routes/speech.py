"""Playing a stored coach reply back as speech.

One route. It takes no text: the words come from the turn row, so holding a
valid token does not buy you a general-purpose speech synthesizer.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import owned_session
from app.db.models import Session as SessionRow
from app.db.session import get_db
from app.services.speech_replay import speech_for_turn

router = APIRouter(prefix="/api", tags=["speech"])


@router.get(
    "/sessions/{session_id}/turns/{turn_id}/speech",
    response_class=Response,
    responses={200: {"content": {"audio/wav": {}}, "description": "Spoken reply"}},
)
async def turn_speech(
    turn_id: int,
    session: SessionRow = Depends(owned_session),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Return the reply as a complete WAV.

    Whole-file rather than streamed: a replay is short and the caller wants a
    seekable buffer it can stop cleanly. A second streaming transport would buy
    nothing here.
    """
    audio = await speech_for_turn(db, session.id, turn_id)
    return Response(
        content=audio,
        media_type="audio/wav",
        headers={
            # Private: this is one account's practice conversation.
            "Cache-Control": "private, max-age=3600",
            "Content-Length": str(len(audio)),
        },
    )
