"""Text-to-Speech (A8) — Kokoro-82M.

Optional in the brief, but load-bearing here. Kokoro exposes a speed parameter,
and that is what turns the acoustic branch into something a listener can *hear*:
when the analyzer reports a long block, the coach slows its own delivery instead
of replying at the same brisk pace. Matching a struggling speaker's tempo is the
clearest behavioural difference between this and an ordinary voice assistant,
and it is the moment the demo should land on.

82M parameters, so it runs on CPU in a few hundred milliseconds and leaves the
GPU entirely to Moshi.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from typing import Any

import numpy as np

from app.core.config import settings
from app.core.errors import DependencyMissingError, ModelUnavailableError
from app.core.logging import get_logger
from app.schemas.acoustic import AcousticProfile

log = get_logger(__name__)

#: Split on sentence boundaries so audio starts playing before the whole reply
#: is synthesized. Keeps time-to-first-audio inside the cascade budget.
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'])")


def split_for_streaming(text: str, *, max_chars: int = 220) -> list[str]:
    """Break a reply into synthesis-sized pieces at natural boundaries."""
    text = text.strip()
    if not text:
        return []

    chunks: list[str] = []
    for sentence in _SENTENCE.split(text):
        sentence = sentence.strip()
        if not sentence:
            continue
        if len(sentence) <= max_chars:
            chunks.append(sentence)
            continue
        # Over-long sentence: fall back to comma boundaries, then hard wrap.
        buf = ""
        for part in re.split(r"(?<=,)\s+", sentence):
            if len(buf) + len(part) + 1 <= max_chars:
                buf = f"{buf} {part}".strip()
            else:
                if buf:
                    chunks.append(buf)
                buf = part if len(part) <= max_chars else part[:max_chars]
        if buf:
            chunks.append(buf)
    return chunks


class TTSService:
    """Lazily-loaded Kokoro wrapper."""

    def __init__(self) -> None:
        self._pipeline: Any | None = None
        self._lock = asyncio.Lock()

    @property
    def loaded(self) -> bool:
        return self._pipeline is not None

    async def _ensure_pipeline(self) -> Any:
        if self._pipeline is not None:
            return self._pipeline

        async with self._lock:
            if self._pipeline is not None:
                return self._pipeline
            try:
                from kokoro import KPipeline
            except ImportError as exc:
                raise DependencyMissingError("kokoro", "Text-to-speech") from exc

            log.info("loading kokoro", extra={"voice": settings.tts_voice})
            try:
                # lang_code 'a' selects the American English voice pack, which
                # is the one af_* voices belong to.
                self._pipeline = await asyncio.to_thread(KPipeline, lang_code="a")
            except Exception as exc:
                raise ModelUnavailableError(
                    "The Kokoro voice could not be loaded. The first run "
                    "downloads it, so check the network connection."
                ) from exc

            log.info("kokoro ready")
            return self._pipeline

    def rate_for(self, profile: AcousticProfile | None) -> float:
        """Choose a delivery rate from the speaker's acoustic profile.

        This is the acoustic branch reaching the output side of the product.
        """
        if profile is None:
            return settings.tts_speed_default
        return profile.suggested_speech_rate(
            floor=settings.tts_speed_min, ceiling=settings.tts_speed_max
        )

    async def synthesize(
        self, text: str, *, speed: float | None = None
    ) -> tuple[np.ndarray, int]:
        """Synthesize a whole reply. Returns mono float32 plus its sample rate."""
        chunks: list[np.ndarray] = []
        async for chunk, _ in self.stream(text, speed=speed):
            chunks.append(chunk)
        if not chunks:
            return np.zeros(0, dtype=np.float32), settings.tts_sample_rate
        return np.concatenate(chunks), settings.tts_sample_rate

    async def stream(
        self, text: str, *, speed: float | None = None
    ) -> AsyncIterator[tuple[np.ndarray, str]]:
        """Yield (audio, text) per sentence as each is synthesized."""
        pieces = split_for_streaming(text)
        if not pieces:
            return

        pipeline = await self._ensure_pipeline()
        rate = float(
            np.clip(
                speed if speed is not None else settings.tts_speed_default,
                settings.tts_speed_min,
                settings.tts_speed_max,
            )
        )

        for piece in pieces:
            audio = await asyncio.to_thread(self._synth_one, pipeline, piece, rate)
            if audio.size:
                yield audio, piece

    def _synth_one(self, pipeline: Any, text: str, speed: float) -> np.ndarray:
        parts: list[np.ndarray] = []
        for result in pipeline(text, voice=settings.tts_voice, speed=speed):
            # Kokoro yields (graphemes, phonemes, audio); older builds expose
            # the tensor as `.audio` on a result object instead.
            audio = result[2] if isinstance(result, tuple) else getattr(result, "audio", None)
            if audio is None:
                continue
            if hasattr(audio, "detach"):
                audio = audio.detach().cpu().numpy()
            parts.append(np.asarray(audio, dtype=np.float32).reshape(-1))

        if not parts:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(parts)

    async def warmup(self) -> bool:
        try:
            await self._ensure_pipeline()
            return True
        except Exception as exc:
            log.warning("kokoro warmup skipped", extra={"reason": str(exc)})
            return False


tts_service = TTSService()
