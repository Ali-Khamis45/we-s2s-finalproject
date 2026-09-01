"""Utterance endpointing.

Both sockets need the same question answered: has this person finished speaking?
On the live path it decides when to run the acoustic analyzer over the user's
audio; on the knowledge path it decides when to transcribe and reply.

Energy-based with hysteresis, and adaptive: the noise floor is estimated from
the audio itself, so a quiet room and a noisy one both work without the user
touching a threshold. WebRTC VAD is better at this and is in the dependency set,
but it only accepts 10/20/30 ms frames at four fixed rates, and it is one more
thing to break on a fresh clone — this needs nothing but numpy.

Endpointing latency is charged to the user's perceived response time, so
`silence_ms` sits at 700: long enough not to cut off someone mid-block, short
enough not to feel unresponsive. That tension is real for this project
specifically — a speaker who blocks for 1.5 s must not be interrupted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto

import numpy as np

from app.services.audio import rms_dbfs


class State(Enum):
    IDLE = auto()      # nothing heard yet
    SPEAKING = auto()  # speech in progress
    TRAILING = auto()  # gone quiet, waiting to see if it resumes


@dataclass(slots=True)
class Endpointer:
    """Streaming endpoint detector. Feed it chunks; it tells you when to act."""

    sample_rate: int
    #: Silence this long after speech closes the utterance. Generous on purpose:
    #: a silent block is exactly the signal this project must not truncate.
    silence_ms: int = 700
    #: Ignore bursts shorter than this — a cough or a keyboard click.
    min_speech_ms: int = 250
    #: Speech is this many dB above the estimated noise floor.
    margin_db: float = 9.0
    #: Absolute floor, so a silent room cannot make the detector trigger on hiss.
    floor_dbfs: float = -52.0

    state: State = State.IDLE
    speech_ms: int = 0
    silence_run_ms: int = 0
    _noise_dbfs: float = -60.0
    _calibrated: bool = False
    _history: list[float] = field(default_factory=list)

    def reset(self) -> None:
        self.state = State.IDLE
        self.speech_ms = 0
        self.silence_run_ms = 0

    @property
    def threshold_dbfs(self) -> float:
        return max(self.floor_dbfs, self._noise_dbfs + self.margin_db)

    def _update_noise(self, level: float) -> None:
        """Track the noise floor from the quietest recent frames."""
        if level == float("-inf"):
            return
        self._history.append(level)
        if len(self._history) > 100:
            self._history.pop(0)
        if len(self._history) >= 8:
            quiet = sorted(self._history)[: max(3, len(self._history) // 4)]
            estimate = float(np.mean(quiet))
            # Fall fast toward a quieter floor, rise slowly, so one loud frame
            # cannot desensitise the detector for the rest of the turn.
            weight = 0.25 if estimate < self._noise_dbfs else 0.05
            self._noise_dbfs = (1 - weight) * self._noise_dbfs + weight * estimate
            self._calibrated = True

    def push(self, samples: np.ndarray) -> bool:
        """Feed one chunk. Returns True when an utterance has just ended."""
        if samples.size == 0:
            return False

        chunk_ms = int(samples.size / self.sample_rate * 1000)
        level = rms_dbfs(samples)
        self._update_noise(level)

        is_speech = level > self.threshold_dbfs

        if is_speech:
            self.silence_run_ms = 0
            self.speech_ms += chunk_ms
            self.state = State.SPEAKING
            return False

        if self.state is State.IDLE:
            return False

        self.silence_run_ms += chunk_ms
        self.state = State.TRAILING

        if self.silence_run_ms >= self.silence_ms:
            complete = self.speech_ms >= self.min_speech_ms
            self.reset()
            return complete

        return False

    @property
    def heard_speech(self) -> bool:
        return self.speech_ms >= self.min_speech_ms
