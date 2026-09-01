"""Speech-to-Text (A7) — Whisper via faster-whisper.

Satisfies the brief's STT requirement and feeds the Knowledge Mode cascade.

Two things worth knowing about how it is used here. It runs on CPU by design:
the GPU is reserved for Moshi, and `small` with int8 on CTranslate2 transcribes
a short utterance in roughly 150–300 ms, which fits the ~1 s cascade budget.
And its output is deliberately *not* the whole story — the transcript loses the
dysfluency that this project is about, which is why `DysfluencyAnalyzer` reads
the same audio in parallel.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from app.core.config import settings
from app.core.errors import DependencyMissingError, ModelUnavailableError
from app.core.logging import get_logger
from app.services.audio import resample

log = get_logger(__name__)


@dataclass(slots=True)
class Word:
    text: str
    start_ms: int
    end_ms: int


@dataclass(slots=True)
class Transcript:
    text: str = ""
    language: str | None = None
    words: list[Word] = field(default_factory=list)
    duration_ms: int = 0

    @property
    def is_empty(self) -> bool:
        return not self.text.strip()

    def word_count(self) -> int:
        return len(self.words) or len(self.text.split())

    def speech_rate_wpm(self) -> float | None:
        """Words per minute over the spoken span.

        Measured across the first to last word rather than the whole buffer, so
        leading and trailing silence do not deflate the figure.
        """
        if self.word_count() == 0:
            return None
        if self.words:
            span = self.words[-1].end_ms - self.words[0].start_ms
        else:
            span = self.duration_ms
        if span <= 0:
            return None
        return round(self.word_count() / (span / 60_000.0), 1)

    def pauses_ms(self) -> list[int]:
        """Gaps between consecutive words.

        A silent block shows up here as an unusually long gap, which is one of
        the two independent signals the analyzer uses.
        """
        return [
            gap
            for prev, nxt in zip(self.words, self.words[1:])
            if (gap := nxt.start_ms - prev.end_ms) > 0
        ]


class STTService:
    """Lazily-loaded Whisper wrapper.

    The model is loaded on first use, not at import, so the API starts on a
    machine that has never downloaded weights. Loading is guarded by a lock
    because two concurrent WebSocket turns would otherwise both try.
    """

    def __init__(self) -> None:
        self._model: Any | None = None
        self._lock = asyncio.Lock()

    @property
    def loaded(self) -> bool:
        return self._model is not None

    async def _ensure_model(self) -> Any:
        if self._model is not None:
            return self._model

        async with self._lock:
            if self._model is not None:
                return self._model

            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:
                raise DependencyMissingError("faster-whisper", "Speech-to-text") from exc

            log.info(
                "loading whisper",
                extra={
                    "model": settings.whisper_model,
                    "device": settings.whisper_device,
                    "compute": settings.whisper_compute_type,
                },
            )
            try:
                self._model = await asyncio.to_thread(
                    WhisperModel,
                    settings.whisper_model,
                    device=settings.whisper_device,
                    compute_type=settings.whisper_compute_type,
                )
            except Exception as exc:
                raise ModelUnavailableError(
                    f"Whisper model '{settings.whisper_model}' could not be loaded. "
                    "The first run downloads it, so check the network connection."
                ) from exc

            log.info("whisper ready", extra={"model": settings.whisper_model})
            return self._model

    async def transcribe(
        self, samples: np.ndarray, sample_rate: int, *, with_words: bool = True
    ) -> Transcript:
        """Transcribe mono float32 audio.

        Word timestamps are on by default: the prosody metrics in the acoustic
        profile are derived from them, so turning them off costs the coach its
        sense of pacing.
        """
        if samples.size == 0:
            return Transcript()

        model = await self._ensure_model()
        audio = resample(samples, sample_rate, settings.stt_sample_rate)
        duration = int(round(audio.size / settings.stt_sample_rate * 1000))

        def _run() -> Transcript:
            segments, info = model.transcribe(
                audio,
                language=settings.whisper_language,
                word_timestamps=with_words,
                vad_filter=True,
                beam_size=1,  # greedy: the cascade budget is ~1 s end to end
                condition_on_previous_text=False,
            )

            parts: list[str] = []
            words: list[Word] = []
            for seg in segments:
                parts.append(seg.text)
                for w in getattr(seg, "words", None) or []:
                    words.append(
                        Word(
                            text=w.word.strip(),
                            start_ms=int(w.start * 1000),
                            end_ms=int(w.end * 1000),
                        )
                    )

            return Transcript(
                text=" ".join(p.strip() for p in parts).strip(),
                language=getattr(info, "language", None),
                words=words,
                duration_ms=duration,
            )

        return await asyncio.to_thread(_run)

    async def warmup(self) -> bool:
        """Load the model ahead of the first real turn.

        Called at startup when eager loading is wanted; failures are logged and
        swallowed so a missing model never blocks the server from booting.
        """
        try:
            await self._ensure_model()
            return True
        except Exception as exc:
            log.warning("whisper warmup skipped", extra={"reason": str(exc)})
            return False


stt_service = STTService()
