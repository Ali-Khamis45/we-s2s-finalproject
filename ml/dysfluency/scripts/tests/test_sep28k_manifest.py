from __future__ import annotations

import csv
from pathlib import Path

import pytest

from sep28k_manifest import (
    LABEL_COLUMNS,
    ClipLabel,
    binarize,
    load_labels,
    passes_quality_filter,
)

CSV_HEADER = (
    "Show,EpId,ClipId,Start,Stop,Unsure,PoorAudioQuality,Prolongation,Block,"
    "SoundRep,WordRep,DifficultToUnderstand,Interjection,NoStutteredWords,"
    "NaturalPause,Music,NoSpeech"
)


def _row(
    show="HeStutters",
    ep_id="0",
    clip_id="0",
    unsure=0,
    poor_audio=0,
    prolongation=0,
    block=0,
    sound_rep=0,
    word_rep=0,
    interjection=0,
    music=0,
    no_speech=0,
):
    return (
        f"{show}, {ep_id}, {clip_id}, 0, 48000, {unsure}, {poor_audio}, "
        f"{prolongation}, {block}, {sound_rep}, {word_rep}, 0, {interjection}, "
        f"0, 0, {music}, {no_speech}"
    )


def write_csv(tmp_path: Path, rows: list[str]) -> Path:
    csv_path = tmp_path / "labels.csv"
    csv_path.write_text(CSV_HEADER + "\n" + "\n".join(rows) + "\n", encoding="utf-8")
    return csv_path


def test_label_columns_order():
    assert LABEL_COLUMNS == ["Block", "Prolongation", "SoundRep", "WordRep", "Interjection"]


def test_load_labels_parses_row_and_resolves_clip_path(tmp_path):
    csv_path = write_csv(tmp_path, [_row(show="HeStutters", ep_id="0", clip_id="3", block=1)])
    clips = load_labels(csv_path, clips_root=Path("data/sep28k/audio/clips"))

    assert len(clips) == 1
    clip = clips[0]
    assert clip.show == "HeStutters"
    assert clip.ep_id == "0"
    assert clip.clip_id == "3"
    assert clip.labels["Block"] == 1
    assert clip.clip_path == Path("data/sep28k/audio/clips/HeStutters/0/HeStutters_0_3.wav")


def test_load_labels_strips_leading_whitespace_from_show(tmp_path):
    csv_path = write_csv(tmp_path, [_row(show="HeStutters")])
    clips = load_labels(csv_path, clips_root=Path("data/sep28k/audio/clips"))
    assert clips[0].show == "HeStutters"


@pytest.mark.parametrize(
    "kwargs,expected",
    [
        ({}, True),
        ({"unsure": 1}, False),
        ({"poor_audio": 2}, False),
        ({"music": 1}, False),
        ({"no_speech": 3}, False),
        ({"unsure": 1, "music": 1}, False),
    ],
)
def test_passes_quality_filter(tmp_path, kwargs, expected):
    csv_path = write_csv(tmp_path, [_row(**kwargs)])
    clip = load_labels(csv_path, clips_root=Path("data/sep28k/audio/clips"))[0]
    assert passes_quality_filter(clip) is expected


def test_binarize_returns_label_columns_order(tmp_path):
    csv_path = write_csv(
        tmp_path,
        [_row(block=2, prolongation=0, sound_rep=1, word_rep=0, interjection=3)],
    )
    clip = load_labels(csv_path, clips_root=Path("data/sep28k/audio/clips"))[0]
    assert binarize(clip) == [1, 0, 1, 0, 1]


def test_binarize_all_zero(tmp_path):
    csv_path = write_csv(tmp_path, [_row()])
    clip = load_labels(csv_path, clips_root=Path("data/sep28k/audio/clips"))[0]
    assert binarize(clip) == [0, 0, 0, 0, 0]


def _make_clip(show, ep_id, clip_id):
    return ClipLabel(
        show=show,
        ep_id=ep_id,
        clip_id=clip_id,
        labels={col: 0 for col in ["Block", "Prolongation", "SoundRep", "WordRep", "Interjection", "PoorAudioQuality", "NoSpeech", "Music", "Unsure"]},
        clip_path=Path(f"data/sep28k/audio/clips/{show}/{ep_id}/{show}_{ep_id}_{clip_id}.wav"),
    )


def test_split_episodes_no_leakage_across_splits():
    from sep28k_manifest import split_episodes

    clips = []
    for show in ["ShowA", "ShowB"]:
        for ep in range(20):
            for clip_id in range(5):
                clips.append(_make_clip(show, str(ep), str(clip_id)))

    splits = split_episodes(clips, seed=42)

    episode_to_splits: dict[tuple[str, str], set[str]] = {}
    for split_name, split_clips in splits.items():
        for clip in split_clips:
            key = (clip.show, clip.ep_id)
            episode_to_splits.setdefault(key, set()).add(split_name)

    leaked = {k: v for k, v in episode_to_splits.items() if len(v) > 1}
    assert leaked == {}


def test_split_episodes_every_show_in_every_split():
    from sep28k_manifest import split_episodes

    clips = []
    for show in ["ShowA", "ShowB", "ShowC"]:
        for ep in range(20):
            clips.append(_make_clip(show, str(ep), "0"))

    splits = split_episodes(clips, seed=42)

    for split_name, split_clips in splits.items():
        shows_present = {clip.show for clip in split_clips}
        assert shows_present == {"ShowA", "ShowB", "ShowC"}, f"{split_name} missing a show"


def test_split_episodes_deterministic():
    from sep28k_manifest import split_episodes

    clips = [_make_clip("ShowA", str(ep), "0") for ep in range(30)]
    splits_a = split_episodes(clips, seed=42)
    splits_b = split_episodes(clips, seed=42)

    assert [c.ep_id for c in splits_a["train"]] == [c.ep_id for c in splits_b["train"]]
