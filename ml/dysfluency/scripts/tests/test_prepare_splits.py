from __future__ import annotations

import csv
from pathlib import Path

from prepare_splits import write_manifest
from sep28k_manifest import ClipLabel


def test_write_manifest_schema_and_content(tmp_path):
    clips = [
        ClipLabel(
            show="HeStutters",
            ep_id="0",
            clip_id="1",
            labels={
                "Block": 1,
                "Prolongation": 0,
                "SoundRep": 0,
                "WordRep": 0,
                "Interjection": 2,
                "PoorAudioQuality": 0,
                "NoSpeech": 0,
                "Music": 0,
                "Unsure": 0,
            },
            clip_path=Path("data/sep28k/audio/clips/HeStutters/0/HeStutters_0_1.wav"),
        )
    ]
    out_path = tmp_path / "train.csv"

    write_manifest(clips, out_path)

    with open(out_path, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))

    assert len(rows) == 1
    row = rows[0]
    assert row["clip_path"] == "data/sep28k/audio/clips/HeStutters/0/HeStutters_0_1.wav"
    assert row["Block"] == "1"
    assert row["Prolongation"] == "0"
    assert row["SoundRep"] == "0"
    assert row["WordRep"] == "0"
    assert row["Interjection"] == "1"
