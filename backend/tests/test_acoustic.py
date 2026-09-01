"""The acoustic-tag contract (S1/M5).

These are the tests Track M should run against the classifier's output too — if
they pass on both sides, the two tracks have not drifted.
"""

from __future__ import annotations

import pytest

from app.schemas.acoustic import (
    SCHEMA_VERSION,
    AcousticProfile,
    DysfluencyEvent,
    DysfluencyKind,
    ProsodyMetrics,
)


def profile(**kw) -> AcousticProfile:
    kw.setdefault("analyzed", True)
    kw.setdefault("duration_ms", 4_000)
    return AcousticProfile(**kw)


class TestFluencyLoad:
    def test_overlapping_events_are_merged_not_summed(self):
        """Two detectors firing on one moment must not double-count it.

        Without merging, a block and a prolongation over the same 1 s of audio
        would report 2 s of dysfluency in a 4 s utterance — and the coach would
        slow down twice as much as the speech warrants.
        """
        p = profile(
            events=[
                DysfluencyEvent(kind=DysfluencyKind.BLOCK, start_ms=1_000, end_ms=2_000),
                DysfluencyEvent(
                    kind=DysfluencyKind.PROLONGATION, start_ms=1_200, end_ms=1_800
                ),
            ]
        )
        assert p.dysfluent_ms == 1_000
        assert p.fluency_load == 0.25

    def test_disjoint_events_accumulate(self):
        p = profile(
            events=[
                DysfluencyEvent(kind=DysfluencyKind.BLOCK, start_ms=0, end_ms=500),
                DysfluencyEvent(
                    kind=DysfluencyKind.WORD_REPETITION, start_ms=2_000, end_ms=2_500
                ),
            ]
        )
        assert p.dysfluent_ms == 1_000

    def test_unanalyzed_profile_reports_no_load(self):
        """An absent analyzer must not look like fluent speech."""
        p = AcousticProfile.unavailable()
        assert p.fluency_load == 0.0
        assert p.analyzed is False
        assert p.to_prompt_block() == ""

    def test_load_is_capped_at_one(self):
        p = profile(
            duration_ms=1_000,
            events=[
                DysfluencyEvent(kind=DysfluencyKind.BLOCK, start_ms=0, end_ms=5_000)
            ],
        )
        assert p.fluency_load == 1.0


class TestSpeechRate:
    """The rate mapping is what a listener actually hears, so it is pinned."""

    def test_fluent_speech_keeps_normal_pace(self):
        p = profile(events=[])
        assert p.suggested_speech_rate(floor=0.75, ceiling=1.15) == 1.0

    def test_heavy_dysfluency_slows_to_the_floor(self):
        p = profile(
            duration_ms=1_000,
            events=[
                DysfluencyEvent(kind=DysfluencyKind.BLOCK, start_ms=0, end_ms=400)
            ],
        )
        assert p.suggested_speech_rate(floor=0.75, ceiling=1.15) == 0.75

    def test_rate_decreases_monotonically_with_load(self):
        rates = []
        for end in (0, 200, 400, 700, 1_000):
            p = profile(
                duration_ms=2_000,
                events=(
                    [DysfluencyEvent(kind=DysfluencyKind.BLOCK, start_ms=0, end_ms=end)]
                    if end
                    else []
                ),
            )
            rates.append(p.suggested_speech_rate(floor=0.75, ceiling=1.15))
        assert rates == sorted(rates, reverse=True)

    def test_unanalyzed_audio_does_not_change_pace(self):
        assert AcousticProfile.unavailable().suggested_speech_rate(
            floor=0.75, ceiling=1.15
        ) == 1.0


class TestPromptBlock:
    def test_block_names_events_and_carries_guidance(self):
        p = profile(
            events=[
                DysfluencyEvent(
                    kind=DysfluencyKind.BLOCK, start_ms=800, end_ms=2_200, confidence=0.9
                )
            ],
            prosody=ProsodyMetrics(speech_rate_wpm=82.0),
        )
        block = p.to_prompt_block()
        assert "<acoustic_context>" in block and "</acoustic_context>" in block
        assert "block x1" in block
        assert "82 wpm" in block
        assert "do not fill the pause" in block

    def test_fluent_turn_still_reports_when_prosody_exists(self):
        """Absence of events is information too — it is why the coach speeds up."""
        p = profile(events=[], prosody=ProsodyMetrics(speech_rate_wpm=140.0))
        assert "no dysfluency events" in p.to_prompt_block()

    @pytest.mark.parametrize(
        "variation,expected",
        [(0.05, "flat"), (0.25, "steady"), (0.60, "strained")],
    )
    def test_pitch_is_described_not_numeric(self, variation, expected):
        """The model gets a word, not a coefficient it cannot interpret."""
        p = profile(prosody=ProsodyMetrics(pitch_variation=variation))
        assert f"pitch: {expected}" in p.to_prompt_block()

    def test_short_pauses_are_not_reported(self):
        p = profile(prosody=ProsodyMetrics(longest_pause_ms=200))
        assert "longest pause" not in p.to_prompt_block()


class TestContract:
    def test_schema_version_is_pinned(self):
        assert SCHEMA_VERSION == "1.0"
        assert profile().schema_version == "1.0"

    def test_every_kind_has_a_coaching_hint(self):
        """A new event class without a hint would silently produce no guidance."""
        from app.schemas.acoustic import COACHING_HINT

        for kind in DysfluencyKind:
            assert kind in COACHING_HINT, f"{kind} has no coaching hint"

    def test_round_trips_through_json(self):
        """Profiles are persisted as JSON on the turn row."""
        original = profile(
            events=[
                DysfluencyEvent(kind=DysfluencyKind.INTERJECTION, start_ms=10, end_ms=90)
            ],
            prosody=ProsodyMetrics(speech_rate_wpm=101.5),
        )
        restored = AcousticProfile(**original.model_dump(mode="json"))
        assert restored.fluency_load == original.fluency_load
        assert restored.event_counts == original.event_counts
