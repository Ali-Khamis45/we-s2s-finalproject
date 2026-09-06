"""Shared helpers for turning the SEP-28k label CSV into training manifests.

Reads `SEP-28k_labels.subset4.csv` (produced by the upstream
ml-stuttering-events-dataset tooling, downloaded by `download_sep28k.py`),
applies the standard SEP-28k quality filter, and binarizes the five
dysfluency-type columns this project's classifier (M4) targets.

See docs/superpowers/specs/2026-09-06-m4-dysfluency-classifier-design.md.
"""
from __future__ import annotations

import csv
import random
from dataclasses import dataclass
from pathlib import Path

LABEL_COLUMNS = ["Block", "Prolongation", "SoundRep", "WordRep", "Interjection"]
QUALITY_FILTER_COLUMNS = ["PoorAudioQuality", "NoSpeech", "Music", "Unsure"]
ALL_COUNT_COLUMNS = LABEL_COLUMNS + QUALITY_FILTER_COLUMNS


@dataclass(frozen=True)
class ClipLabel:
    show: str
    ep_id: str
    clip_id: str
    labels: dict[str, int]
    clip_path: Path


def load_labels(csv_path: Path, clips_root: Path) -> list[ClipLabel]:
    """Parse the label CSV into one ClipLabel per row.

    `clip_path` is constructed from the naming convention used by
    `extract_clips.py`: `{clips_root}/{show}/{ep_id}/{show}_{ep_id}_{clip_id}.wav`.
    Existence of the file on disk is NOT checked here -- callers that need
    only labeled clips that were actually downloaded should filter
    separately (see `prepare_splits.py`).
    """
    clips = []
    with open(csv_path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, skipinitialspace=True)
        for row in reader:
            show = row["Show"].strip()
            ep_id = row["EpId"].strip()
            clip_id = row["ClipId"].strip()
            labels = {col: int(row[col]) for col in ALL_COUNT_COLUMNS}
            clip_path = clips_root / show / ep_id / f"{show}_{ep_id}_{clip_id}.wav"
            clips.append(
                ClipLabel(show=show, ep_id=ep_id, clip_id=clip_id, labels=labels, clip_path=clip_path)
            )
    return clips


def passes_quality_filter(clip: ClipLabel) -> bool:
    """True if none of the quality-flag columns are positive."""
    return all(clip.labels[col] == 0 for col in QUALITY_FILTER_COLUMNS)


def binarize(clip: ClipLabel) -> list[int]:
    """Binary labels in LABEL_COLUMNS order: 1 if any annotator flagged it."""
    return [1 if clip.labels[col] > 0 else 0 for col in LABEL_COLUMNS]


def split_episodes(
    clips: list[ClipLabel], seed: int = 42, ratios: tuple[float, float, float] = (0.7, 0.15, 0.15)
) -> dict[str, list[ClipLabel]]:
    """Split by (show, ep_id) so no episode's clips cross a split boundary.

    Stratifies by show: each show's episode list is shuffled and cut
    independently at the given ratios, then the per-show splits are
    concatenated. This keeps all 4 shows represented in every split even
    though they have very different episode counts.
    """
    by_show: dict[str, dict[str, list[ClipLabel]]] = {}
    for clip in clips:
        by_show.setdefault(clip.show, {}).setdefault(clip.ep_id, []).append(clip)

    result: dict[str, list[ClipLabel]] = {"train": [], "val": [], "test": []}
    rng = random.Random(seed)
    for show, episodes_by_id in sorted(by_show.items()):
        ep_ids = sorted(episodes_by_id.keys(), key=lambda x: int(x))
        rng.shuffle(ep_ids)
        n = len(ep_ids)
        n_train = round(n * ratios[0])
        n_val = round(n * ratios[1])
        train_ids = ep_ids[:n_train]
        val_ids = ep_ids[n_train : n_train + n_val]
        test_ids = ep_ids[n_train + n_val :]
        for split_name, ids in (("train", train_ids), ("val", val_ids), ("test", test_ids)):
            for ep_id in ids:
                result[split_name].extend(episodes_by_id[ep_id])
    return result
