"""Replaying a stored coach reply as speech.

Audio is never persisted — see `docs/ETHICS.md` — so a replay re-synthesizes
from the turn's text every time. The rate is the part that matters: it comes
from the acoustic profile of the utterance the coach was answering, so a replay
sounds the way the reply sounded when it was produced. Replaying at a flat
default would misrepresent the one behaviour that distinguishes this product
from an ordinary voice assistant.

Text always comes from the database row, never from the request, which keeps
the endpoint from becoming an open text-to-speech service for anyone holding a
token.
"""

from __future__ import annotations

from collections import OrderedDict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import NotFoundError, ValidationError
from app.db.models import Turn as TurnRow
from app.schemas.acoustic import AcousticProfile
from app.services.audio import wav_bytes
from app.services.tts import tts_service


class ReplayCache:
    """A small in-process LRU of encoded WAVs, keyed by turn and rate.

    Synthesis costs a few hundred milliseconds and a speaker button invites
    repeat presses. Bounded by both entry count and total bytes because a long
    reply is far larger than a short one, so a count alone is a poor proxy for
    memory. Nothing is written to disk: this is a cache, not storage.
    """

    def __init__(self, max_entries: int = 32, max_bytes: int = 64 * 1024 * 1024):
        self._items: OrderedDict[tuple[int, float], bytes] = OrderedDict()
        self._max_entries = max_entries
        self._max_bytes = max_bytes
        self._bytes = 0

    def get(self, key: tuple[int, float]) -> bytes | None:
        if (hit := self._items.get(key)) is None:
            return None
        self._items.move_to_end(key)
        return hit

    def put(self, key: tuple[int, float], value: bytes) -> None:
        if key in self._items:
            self._bytes -= len(self._items.pop(key))
        self._items[key] = value
        self._bytes += len(value)
        while self._items and (
            len(self._items) > self._max_entries or self._bytes > self._max_bytes
        ):
            _, evicted = self._items.popitem(last=False)
            self._bytes -= len(evicted)

    def clear(self) -> None:
        self._items.clear()
        self._bytes = 0


replay_cache = ReplayCache()


async def _rate_for_turn(db: AsyncSession, turn: TurnRow) -> float:
    """The delivery rate this reply was, or would have been, spoken at.

    Derived from the neighbouring user turn rather than stored on the coach
    turn, so it also works for rows written before replay existed.
    """
    previous = (
        await db.execute(
            select(TurnRow)
            .where(
                TurnRow.session_id == turn.session_id,
                TurnRow.id < turn.id,
                TurnRow.role == "user",
            )
            .order_by(TurnRow.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    if previous is None or not previous.acoustic:
        return settings.tts_speed_default
    return tts_service.rate_for(AcousticProfile(**previous.acoustic))


async def speech_for_turn(db: AsyncSession, session_id: str, turn_id: int) -> bytes:
    """Encoded WAV for one coach turn. Raises the app's own errors."""
    turn = await db.get(TurnRow, turn_id)
    # Turn ids are a global autoincrement, so the session in the path has to be
    # checked too — otherwise one session you own is a window onto every turn.
    if turn is None or turn.session_id != session_id:
        raise NotFoundError("That reply doesn't exist.")

    if turn.role != "coach":
        raise ValidationError("Only the coach's replies can be played back.")

    text = (turn.text or "").strip()
    if not text:
        raise ValidationError("That reply has no words to speak.")

    rate = await _rate_for_turn(db, turn)
    # Rounded so imperceptible rate differences cannot each take a cache slot.
    key = (turn.id, round(rate, 2))

    if (hit := replay_cache.get(key)) is not None:
        return hit

    samples, sample_rate = await tts_service.synthesize(text, speed=rate)
    encoded = wav_bytes(samples, sample_rate)
    replay_cache.put(key, encoded)
    return encoded
