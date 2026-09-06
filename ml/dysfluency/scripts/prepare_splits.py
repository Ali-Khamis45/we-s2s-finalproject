"""Build train/val/test manifest CSVs for the M4 dysfluency classifier.

Reads the SEP-28k label CSV, drops clips flagged for poor audio quality /
no speech / music / annotator uncertainty (standard SEP-28k practice), drops
clips whose audio file isn't actually present on disk (the M3 4-show subset
has a couple of genuine dead links), binarizes the five target dysfluency
labels, and splits by episode (stratified by show) so no episode's clips
cross a train/val/test boundary.

See docs/superpowers/specs/2026-09-06-m4-dysfluency-classifier-design.md.
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

from sep28k_manifest import LABEL_COLUMNS, ClipLabel, binarize, load_labels, passes_quality_filter, split_episodes

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_LABELS_CSV = REPO_ROOT / "ml" / "dysfluency" / "sep28k-src" / "SEP-28k_labels.subset4.csv"
DEFAULT_CLIPS_ROOT = REPO_ROOT / "data" / "sep28k" / "audio" / "clips"
DEFAULT_OUT_DIR = REPO_ROOT / "data" / "sep28k" / "audio" / "splits"


def write_manifest(clips: list[ClipLabel], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["clip_path", *LABEL_COLUMNS])
        for clip in clips:
            writer.writerow([clip.clip_path.as_posix(), *binarize(clip)])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--labels-csv", type=Path, default=DEFAULT_LABELS_CSV)
    parser.add_argument("--clips-root", type=Path, default=DEFAULT_CLIPS_ROOT)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    clips = load_labels(args.labels_csv, clips_root=args.clips_root)
    print(f"Loaded {len(clips)} labeled clips from {args.labels_csv}")

    quality_filtered = [c for c in clips if passes_quality_filter(c)]
    print(f"After quality filter: {len(quality_filtered)} clips ({len(clips) - len(quality_filtered)} dropped)")

    on_disk = [c for c in quality_filtered if c.clip_path.is_file()]
    missing = len(quality_filtered) - len(on_disk)
    if missing:
        print(f"WARNING: {missing} clips have labels but no audio file on disk -- skipping them")

    splits = split_episodes(on_disk, seed=args.seed)
    for split_name, split_clips in splits.items():
        out_path = args.out_dir / f"{split_name}.csv"
        write_manifest(split_clips, out_path)
        print(f"{split_name}: {len(split_clips)} clips -> {out_path}")


if __name__ == "__main__":
    main()
