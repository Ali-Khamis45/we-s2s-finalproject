"""The acoustic-tag contract (tasks S1 / M5).

This is the interface between Track M's dysfluency classifier and Track A's
prompt module. It is the reason the project is not an ordinary cascade: Whisper
turns "I-i-i want... water" into "I want water" and every trace of the block and
the repetition is gone before the language model sees anything. The classifier
reads the same audio in parallel and emits *this* structure, which the prompt
builder (A12) folds back in as context.

Both sides import from here. Changing a field name or an enum value is a
breaking change for the other track — bump SCHEMA_VERSION and say so.

Scope boundary (docs/ETHICS.md): these are acoustic events, not clinical
findings. Nothing here names a condition, grades a severity, or is shown to the
user as an assessment. It exists so the coach can adapt its pacing.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, Field, computed_field

SCHEMA_VERSION = "1.0"


class DysfluencyKind(StrEnum):
    """The five SEP-28k event classes, plus a catch-all.

    Names mirror the SEP-28k label set so Track M's training code and this
    schema stay in step without a translation table.
    """

    BLOCK = "block"
    PROLONGATION = "prolongation"
    SOUND_REPETITION = "sound_repetition"
    WORD_REPETITION = "word_repetition"
    INTERJECTION = "interjection"
    UNSURE = "unsure"


#: How the coach should adapt to each event kind. The prompt builder turns these
#: into instructions; keeping them here means one place defines the response.
COACHING_HINT: dict[DysfluencyKind, str] = {
    DysfluencyKind.BLOCK: (
        "The speaker hit a silent block. Give them room — do not fill the pause "
        "or finish their sentence. Slow your own delivery."
    ),
    DysfluencyKind.PROLONGATION: (
        "A sound was stretched. Keep your reply unhurried; gentle easy-onset "
        "modelling is appropriate if they asked for technique work."
    ),
    DysfluencyKind.SOUND_REPETITION: (
        "A sound repeated. Acknowledge the content, never the repetition, "
        "unless they explicitly asked for feedback on fluency."
    ),
    DysfluencyKind.WORD_REPETITION: (
        "A whole word repeated. Respond to what they meant; do not mirror it."
    ),
    DysfluencyKind.INTERJECTION: (
        "Filler words appeared. Common under pressure and rarely worth naming "
        "unless the session's focus is filler reduction."
    ),
    DysfluencyKind.UNSURE: (
        "The signal was ambiguous. Do not act on it; respond to the content."
    ),
}

Confidence = Annotated[float, Field(ge=0.0, le=1.0)]


class DysfluencyEvent(BaseModel):
    """One detected acoustic event, located in time within the utterance."""

    kind: DysfluencyKind
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    confidence: Confidence = 1.0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def duration_ms(self) -> int:
        return max(0, self.end_ms - self.start_ms)


class ProsodyMetrics(BaseModel):
    """Continuous delivery measurements over the whole utterance.

    These carry the information a transcript cannot: how fast, how evenly, and
    with how much pitch movement the speaker delivered the words.
    """

    speech_rate_wpm: float | None = Field(default=None, ge=0)
    articulation_rate_sps: float | None = Field(default=None, ge=0)
    mean_pause_ms: float | None = Field(default=None, ge=0)
    longest_pause_ms: int | None = Field(default=None, ge=0)
    pitch_mean_hz: float | None = Field(default=None, ge=0)
    #: Coefficient of variation. Low values read as flat or guarded delivery,
    #: very high values as strain — neither is "bad", both are worth pacing to.
    pitch_variation: float | None = Field(default=None, ge=0)
    energy_variation: float | None = Field(default=None, ge=0)


class AcousticProfile(BaseModel):
    """Everything the acoustic branch knows about one user utterance."""

    schema_version: str = SCHEMA_VERSION
    duration_ms: int = Field(default=0, ge=0)
    events: list[DysfluencyEvent] = Field(default_factory=list)
    prosody: ProsodyMetrics = Field(default_factory=ProsodyMetrics)
    #: False when the analyzer did not run (model absent, audio too short).
    #: The prompt builder omits acoustic context entirely rather than guessing.
    analyzed: bool = False
    source: str = "unavailable"

    # ---- derived views -------------------------------------------------

    @computed_field  # type: ignore[prop-decorator]
    @property
    def event_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for e in self.events:
            counts[e.kind.value] = counts.get(e.kind.value, 0) + 1
        return counts

    @computed_field  # type: ignore[prop-decorator]
    @property
    def dysfluent_ms(self) -> int:
        """Total time occupied by detected events.

        Overlapping events are merged so two detectors firing on one moment do
        not double-count it.
        """
        if not self.events:
            return 0
        spans = sorted((e.start_ms, e.end_ms) for e in self.events)
        total = 0
        cur_start, cur_end = spans[0]
        for start, end in spans[1:]:
            if start <= cur_end:
                cur_end = max(cur_end, end)
            else:
                total += cur_end - cur_start
                cur_start, cur_end = start, end
        return total + (cur_end - cur_start)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def fluency_load(self) -> float:
        """Share of the utterance occupied by dysfluency events, 0.0–1.0.

        Deliberately *not* a severity score. It drives one thing: how far the
        coach slows its own voice. See `suggested_speech_rate`.
        """
        if not self.analyzed or self.duration_ms <= 0:
            return 0.0
        return round(min(1.0, self.dysfluent_ms / self.duration_ms), 4)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def dominant_event(self) -> str | None:
        """The event kind that occupied the most time.

        Weighted by duration, not by count. Counting would let three 400 ms
        repetitions outvote a single 1.5 s block, and the coach would be told
        to watch for repeated words when what actually happened is that the
        speaker got stuck — the one situation where "give them room, do not
        fill the pause" matters most. Time occupied is the better proxy for
        what dominated the utterance, and it still surfaces filler guidance for
        someone whose speech is mostly interjections.
        """
        if not self.events:
            return None

        by_kind: dict[str, tuple[int, int]] = {}
        for e in self.events:
            total_ms, count = by_kind.get(e.kind.value, (0, 0))
            by_kind[e.kind.value] = (total_ms + e.duration_ms, count + 1)

        # Count breaks ties, so zero-length events still resolve deterministically.
        return max(by_kind.items(), key=lambda kv: (kv[1][0], kv[1][1]))[0]

    def suggested_speech_rate(self, *, floor: float, ceiling: float) -> float:
        """Map fluency load onto a TTS rate multiplier.

        Speaking more slowly back to someone who is struggling is the single
        clearest way the acoustic branch changes the product's behaviour, and
        it is the thing a demo can hear. A long block pulls the rate toward
        `floor`; a clean, fluent turn lets it sit at normal pace.
        """
        if not self.analyzed:
            return 1.0
        load = self.fluency_load
        rate = 1.0 - (1.0 - floor) * min(1.0, load / 0.35)
        return round(max(floor, min(ceiling, rate)), 3)

    def to_prompt_block(self) -> str:
        """Render as the structured context block consumed by A12.

        Returns an empty string when nothing was analyzed, so the prompt builder
        can concatenate unconditionally.
        """
        # Render whenever the analyzer produced anything at all. Gating on
        # events plus speech rate alone would silently drop a prosody-only
        # reading — flat or strained delivery with no discrete events is real
        # information about how someone is speaking, and it is exactly the
        # signal a transcript cannot carry.
        has_prosody = any(
            v is not None for v in self.prosody.model_dump().values()
        )
        if not self.analyzed or (not self.events and not has_prosody):
            return ""

        lines = ["<acoustic_context>"]

        if self.events:
            counts = self.event_counts
            summary = ", ".join(f"{k.replace('_', ' ')} x{v}" for k, v in sorted(counts.items()))
            lines.append(f"detected: {summary}")

            longest = max(self.events, key=lambda e: e.duration_ms)
            if longest.duration_ms >= 400:
                lines.append(
                    f"longest event: {longest.kind.value.replace('_', ' ')} "
                    f"lasting {longest.duration_ms} ms"
                )
        else:
            lines.append("detected: no dysfluency events")

        p = self.prosody
        if p.speech_rate_wpm is not None:
            lines.append(f"speech rate: {p.speech_rate_wpm:.0f} wpm")
        if p.longest_pause_ms is not None and p.longest_pause_ms >= 500:
            lines.append(f"longest pause: {p.longest_pause_ms} ms")
        if p.pitch_variation is not None:
            texture = (
                "flat" if p.pitch_variation < 0.10
                else "strained" if p.pitch_variation > 0.45
                else "steady"
            )
            lines.append(f"pitch: {texture}")

        # NO guidance line here. It used to sit in this block, and evaluation
        # showed the model echoing it straight back to the user — replies like
        # "give them room, don't fill the pause" addressed to the speaker, who
        # is not the one who needs that instruction. Anything inside the user
        # turn reads as content to relay; an instruction about how to respond
        # belongs in the system role. See `coaching_directive`.
        lines.append("</acoustic_context>")
        return "\n".join(lines)

    def coaching_directive(self) -> str | None:
        """How the coach should adapt, phrased as an instruction to the model.

        Kept out of `to_prompt_block` on purpose: the prompt builder puts this
        in the system message, where the model treats it as a rule rather than
        as something to say out loud.
        """
        if not self.analyzed:
            return None
        dom = self.dominant_event
        return COACHING_HINT[DysfluencyKind(dom)] if dom else None

    @classmethod
    def unavailable(cls) -> AcousticProfile:
        """The profile used when the analyzer did not run."""
        return cls(analyzed=False, source="unavailable")


def json_schema() -> dict:
    """Export for Track M, so the classifier can validate against this contract."""
    return AcousticProfile.model_json_schema()
