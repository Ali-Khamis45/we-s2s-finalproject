"""The acoustic branch — what a transcript throws away.

This is the half of the cascade that makes the project more than a voice
chatbot. Whisper flattens "I-i-i want... water" into "I want water"; this reads
the same waveform and reports the repetition, the block, and how long it lasted,
as an `AcousticProfile` (see schemas/acoustic.py — the S1/M5 contract).

Two backends implement it:

  Wav2VecBackend    Track M's SEP-28k classifier (M4). The real one.
  HeuristicBackend  Word timings plus frame energy and pitch. No training, no
                    weights, no torch. Deliberately conservative.

The heuristic exists so Track A is not blocked for the weeks it takes to fetch
SEP-28k audio and train a classifier — the timeline overlay (A16), the pacing
logic, and the prompt contract can all be built and demoed against it, and the
trained model drops in behind the same interface. It is a scaffold, not a
result: nothing in the thesis should cite its output.

Scope (docs/ETHICS.md): acoustic events, not clinical findings.
"""

from __future__ import annotations

import asyncio
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import numpy as np

from app.core.config import settings
from app.core.logging import get_logger
from app.schemas.acoustic import (
    AcousticProfile,
    DysfluencyEvent,
    DysfluencyKind,
    ProsodyMetrics,
)
from app.services.audio import resample
from app.services.stt import Transcript

log = get_logger(__name__)

ANALYZER_SAMPLE_RATE = 16_000

#: Filler tokens counted as interjections. English-only, matching the project's
#: scope; Moshi is English-only too, so this is not the limiting factor.
FILLERS = frozenset(
    {"um", "uh", "erm", "er", "ah", "hmm", "mm", "eh", "uhh", "umm"}
)

#: A silent gap at least this long between words reads as a block rather than
#: ordinary phrasing. Conversational pauses cluster well below this.
BLOCK_GAP_MS = 450
#: Sustained single sounds. Whisper reports these as one long, short-text word.
PROLONGATION_MS = 380
PROLONGATION_CHARS = 4


def _normalize(token: str) -> str:
    return re.sub(r"[^a-z']", "", token.lower())


class AnalyzerBackend(ABC):
    name = "base"

    @abstractmethod
    async def analyze(
        self, samples: np.ndarray, sample_rate: int, transcript: Transcript | None
    ) -> AcousticProfile: ...

    async def load(self) -> bool:
        return True


class HeuristicBackend(AnalyzerBackend):
    """Signal- and timing-based estimates. No model weights.

    Detects four of the five SEP-28k classes from evidence that survives into
    Whisper's word timings, and measures prosody from the waveform directly.
    Sound repetitions are not attempted — they need sub-word resolution that
    only the trained classifier has.
    """

    name = "heuristic"

    async def analyze(
        self, samples: np.ndarray, sample_rate: int, transcript: Transcript | None
    ) -> AcousticProfile:
        audio = resample(samples, sample_rate, ANALYZER_SAMPLE_RATE)
        return await asyncio.to_thread(self._analyze_sync, audio, transcript)

    def _analyze_sync(
        self, audio: np.ndarray, transcript: Transcript | None
    ) -> AcousticProfile:
        duration_ms = int(round(audio.size / ANALYZER_SAMPLE_RATE * 1000))
        events: list[DysfluencyEvent] = []

        if transcript and transcript.words:
            events.extend(self._blocks(transcript))
            events.extend(self._prolongations(transcript))
            events.extend(self._word_repetitions(transcript))
            events.extend(self._interjections(transcript))

        prosody = self._prosody(audio, transcript)

        return AcousticProfile(
            duration_ms=duration_ms,
            events=sorted(events, key=lambda e: e.start_ms),
            prosody=prosody,
            analyzed=True,
            source=self.name,
        )

    def _blocks(self, t: Transcript) -> list[DysfluencyEvent]:
        out: list[DysfluencyEvent] = []
        for prev, nxt in zip(t.words, t.words[1:]):
            gap = nxt.start_ms - prev.end_ms
            if gap >= BLOCK_GAP_MS:
                # Confidence grows with the gap and saturates around 1.5 s;
                # beyond that it is unambiguously a block, not phrasing.
                conf = min(1.0, 0.45 + (gap - BLOCK_GAP_MS) / 1_500.0)
                out.append(
                    DysfluencyEvent(
                        kind=DysfluencyKind.BLOCK,
                        start_ms=prev.end_ms,
                        end_ms=nxt.start_ms,
                        confidence=round(conf, 3),
                    )
                )
        return out

    def _prolongations(self, t: Transcript) -> list[DysfluencyEvent]:
        out: list[DysfluencyEvent] = []
        for w in t.words:
            span = w.end_ms - w.start_ms
            token = _normalize(w.text)
            # A short token occupying a long span means the sound was stretched.
            if span >= PROLONGATION_MS and 0 < len(token) <= PROLONGATION_CHARS:
                out.append(
                    DysfluencyEvent(
                        kind=DysfluencyKind.PROLONGATION,
                        start_ms=w.start_ms,
                        end_ms=w.end_ms,
                        confidence=round(min(0.85, 0.4 + span / 2_000.0), 3),
                    )
                )
        return out

    def _word_repetitions(self, t: Transcript) -> list[DysfluencyEvent]:
        out: list[DysfluencyEvent] = []
        for prev, nxt in zip(t.words, t.words[1:]):
            a, b = _normalize(prev.text), _normalize(nxt.text)
            if not a or a != b:
                continue
            # Repeating across a long gap is usually a restart for emphasis or
            # after an interruption, not a dysfluent repetition.
            if nxt.start_ms - prev.end_ms > 700:
                continue
            out.append(
                DysfluencyEvent(
                    kind=DysfluencyKind.WORD_REPETITION,
                    start_ms=prev.start_ms,
                    end_ms=nxt.end_ms,
                    confidence=0.7,
                )
            )
        return out

    def _interjections(self, t: Transcript) -> list[DysfluencyEvent]:
        return [
            DysfluencyEvent(
                kind=DysfluencyKind.INTERJECTION,
                start_ms=w.start_ms,
                end_ms=w.end_ms,
                confidence=0.8,
            )
            for w in t.words
            if _normalize(w.text) in FILLERS
        ]

    def _prosody(self, audio: np.ndarray, t: Transcript | None) -> ProsodyMetrics:
        m = ProsodyMetrics()

        if t is not None:
            m.speech_rate_wpm = t.speech_rate_wpm()
            if pauses := t.pauses_ms():
                m.mean_pause_ms = round(float(np.mean(pauses)), 1)
                m.longest_pause_ms = int(max(pauses))
            if t.words:
                voiced_ms = sum(w.end_ms - w.start_ms for w in t.words)
                if voiced_ms > 0:
                    syllables = sum(
                        max(1, len(re.findall(r"[aeiouy]+", _normalize(w.text))))
                        for w in t.words
                    )
                    m.articulation_rate_sps = round(syllables / (voiced_ms / 1000.0), 2)

        if audio.size >= ANALYZER_SAMPLE_RATE // 10:
            frame = ANALYZER_SAMPLE_RATE // 50  # 20 ms
            usable = audio.size - (audio.size % frame)
            if usable >= frame:
                frames = audio[:usable].reshape(-1, frame)
                energy = np.sqrt(np.mean(np.square(frames, dtype=np.float64), axis=1))
                voiced = energy > max(1e-4, float(np.mean(energy)) * 0.35)
                if voiced.any():
                    e = energy[voiced]
                    mean_e = float(np.mean(e))
                    if mean_e > 0:
                        m.energy_variation = round(float(np.std(e)) / mean_e, 4)

                    pitches = self._pitch_track(frames[voiced])
                    if pitches.size >= 3:
                        mean_p = float(np.mean(pitches))
                        m.pitch_mean_hz = round(mean_p, 1)
                        if mean_p > 0:
                            m.pitch_variation = round(float(np.std(pitches)) / mean_p, 4)
        return m

    def _pitch_track(self, frames: np.ndarray) -> np.ndarray:
        """Per-frame F0 by autocorrelation, restricted to the speech range.

        Crude next to YIN or pYIN, but it needs no extra dependency and only
        feeds a three-way flat/steady/strained label in the prompt.
        """
        lo = ANALYZER_SAMPLE_RATE // 400  # 400 Hz ceiling
        hi = ANALYZER_SAMPLE_RATE // 70   # 70 Hz floor
        out: list[float] = []

        for frame in frames:
            centered = frame - frame.mean()
            if not np.any(centered):
                continue
            corr = np.correlate(centered, centered, mode="full")[centered.size - 1:]
            if corr.size <= hi or corr[0] <= 0:
                continue
            window = corr[lo:hi]
            if window.size == 0:
                continue
            peak = int(np.argmax(window)) + lo
            # Reject frames with no clear periodicity — unvoiced or noise.
            if corr[peak] < 0.3 * corr[0]:
                continue
            out.append(ANALYZER_SAMPLE_RATE / peak)

        return np.asarray(out, dtype=np.float64)


class Wav2VecBackend(AnalyzerBackend):
    """Track M's SEP-28k classifier (M4).

    Expects a directory saved with `save_pretrained` holding a wav2vec2 model
    with a multi-label head whose `id2label` values are `DysfluencyKind` values.
    Inference is windowed at 3 s with 1.5 s hop, matching SEP-28k's clip length,
    and windows above threshold are merged into contiguous events.
    """

    name = "wav2vec2-sep28k"
    WINDOW_S = 3.0
    HOP_S = 1.5

    def __init__(self, model_path: Path) -> None:
        self.model_path = model_path
        self._model: Any | None = None
        self._extractor: Any | None = None
        self._labels: dict[int, str] = {}
        self._lock = asyncio.Lock()
        # Prosody is signal analysis, not classification — reuse it rather than
        # duplicating the DSP here.
        self._prosody = HeuristicBackend()

    async def load(self) -> bool:
        if self._model is not None:
            return True
        async with self._lock:
            if self._model is not None:
                return True
            try:
                import torch  # noqa: F401
                from transformers import (
                    AutoFeatureExtractor,
                    AutoModelForAudioClassification,
                )
            except ImportError:
                log.info("dysfluency classifier needs torch + transformers; using heuristic")
                return False

            try:
                self._extractor = await asyncio.to_thread(
                    AutoFeatureExtractor.from_pretrained, str(self.model_path)
                )
                model = await asyncio.to_thread(
                    AutoModelForAudioClassification.from_pretrained, str(self.model_path)
                )
                model.eval()
                self._model = model
                self._labels = {
                    int(k): str(v) for k, v in (model.config.id2label or {}).items()
                }
            except Exception as exc:
                log.warning("dysfluency model load failed", extra={"reason": str(exc)})
                return False

            log.info("dysfluency classifier ready", extra={"labels": len(self._labels)})
            return True

    async def analyze(
        self, samples: np.ndarray, sample_rate: int, transcript: Transcript | None
    ) -> AcousticProfile:
        if self._model is None and not await self.load():
            return await self._prosody.analyze(samples, sample_rate, transcript)

        audio = resample(samples, sample_rate, ANALYZER_SAMPLE_RATE)
        events = await asyncio.to_thread(self._classify, audio)
        prosody = await self._prosody.analyze(samples, sample_rate, transcript)

        return AcousticProfile(
            duration_ms=int(round(audio.size / ANALYZER_SAMPLE_RATE * 1000)),
            events=events,
            prosody=prosody.prosody,
            analyzed=True,
            source=self.name,
        )

    def _classify(self, audio: np.ndarray) -> list[DysfluencyEvent]:
        import torch

        win = int(self.WINDOW_S * ANALYZER_SAMPLE_RATE)
        hop = int(self.HOP_S * ANALYZER_SAMPLE_RATE)
        if audio.size < win:
            audio = np.pad(audio, (0, win - audio.size))

        starts = list(range(0, max(1, audio.size - win + 1), hop))
        windows = [audio[s : s + win] for s in starts]

        inputs = self._extractor(  # type: ignore[misc]
            windows,
            sampling_rate=ANALYZER_SAMPLE_RATE,
            return_tensors="pt",
            padding=True,
        )
        with torch.no_grad():
            logits = self._model(**inputs).logits  # type: ignore[misc]
        probs = torch.sigmoid(logits).cpu().numpy()

        # Collect per-label hits, then merge windows that touch.
        hits: dict[str, list[tuple[int, int, float]]] = {}
        for w_idx, start in enumerate(starts):
            t0 = int(start / ANALYZER_SAMPLE_RATE * 1000)
            t1 = t0 + int(self.WINDOW_S * 1000)
            for label_idx, score in enumerate(probs[w_idx]):
                if score < settings.dysfluency_threshold:
                    continue
                label = self._labels.get(label_idx, "")
                if label not in DysfluencyKind.__members__.values() and label not in {
                    k.value for k in DysfluencyKind
                }:
                    continue
                hits.setdefault(label, []).append((t0, t1, float(score)))

        events: list[DysfluencyEvent] = []
        for label, spans in hits.items():
            for start_ms, end_ms, score in _merge_spans(sorted(spans)):
                events.append(
                    DysfluencyEvent(
                        kind=DysfluencyKind(label),
                        start_ms=start_ms,
                        end_ms=end_ms,
                        confidence=round(score, 3),
                    )
                )
        return sorted(events, key=lambda e: e.start_ms)


def _merge_spans(
    spans: list[tuple[int, int, float]],
) -> list[tuple[int, int, float]]:
    """Merge overlapping windows, keeping the strongest score."""
    if not spans:
        return []
    merged: list[list[float]] = [list(spans[0])]
    for start, end, score in spans[1:]:
        cur = merged[-1]
        if start <= cur[1]:
            cur[1] = max(cur[1], end)
            cur[2] = max(cur[2], score)
        else:
            merged.append([start, end, score])
    return [(int(a), int(b), c) for a, b, c in merged]


class DysfluencyAnalyzer:
    """Picks a backend once, then delegates.

    Prefers Track M's trained classifier when `SCC_DYSFLUENCY_MODEL_PATH` points
    at one, and falls back to the heuristic otherwise. `profile.source` records
    which ran, so a demo or an evaluation can never silently mistake scaffold
    output for model output.
    """

    def __init__(self) -> None:
        self._backend: AnalyzerBackend | None = None
        self._lock = asyncio.Lock()

    async def _ensure_backend(self) -> AnalyzerBackend:
        if self._backend is not None:
            return self._backend
        async with self._lock:
            if self._backend is not None:
                return self._backend

            path = settings.dysfluency_model_path
            if path and Path(path).exists():
                candidate = Wav2VecBackend(Path(path))
                if await candidate.load():
                    self._backend = candidate
                    return self._backend
                log.warning("falling back to heuristic analyzer", extra={"path": str(path)})

            self._backend = HeuristicBackend()
            log.info("dysfluency backend selected", extra={"backend": self._backend.name})
            return self._backend

    @property
    def backend_name(self) -> str:
        return self._backend.name if self._backend else "unselected"

    async def analyze(
        self,
        samples: np.ndarray,
        sample_rate: int,
        transcript: Transcript | None = None,
    ) -> AcousticProfile:
        # Too short to say anything meaningful; claiming otherwise would put
        # noise into the prompt and the timeline.
        if samples.size < sample_rate * 0.2:
            return AcousticProfile.unavailable()
        try:
            backend = await self._ensure_backend()
            return await backend.analyze(samples, sample_rate, transcript)
        except Exception as exc:
            # The acoustic branch is an enhancement; the conversation must
            # survive its failure.
            log.warning("acoustic analysis failed", extra={"reason": str(exc)})
            return AcousticProfile.unavailable()


dysfluency_analyzer = DysfluencyAnalyzer()
