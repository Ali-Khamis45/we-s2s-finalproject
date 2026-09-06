# M4 Dysfluency Classifier Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Train and evaluate a wav2vec2-base multi-label classifier for five dysfluency types (block, prolongation, sound repetition, word repetition, interjection) on the SEP-28k 4-show subset, reporting per-class F1, and ship a minimal inference wrapper for future backend use.

**Architecture:** A data-prep script builds leakage-free, quality-filtered train/val/test manifests from the existing SEP-28k label CSV and downloaded clips. A training script fine-tunes `facebook/wav2vec2-base` (frozen CNN feature extractor, full transformer + head fine-tune) with a weighted multi-label BCE loss, saving the best checkpoint by validation macro-F1. An evaluation script reports per-class F1/precision/recall/PR-AUC on the held-out test manifest. A separate, dependency-light inference module wraps the trained checkpoint for later reuse.

**Tech Stack:** Python 3.12, PyTorch 2.11.0+cu128 (RTX 5050 Laptop GPU, sm_120, confirmed working), `transformers`, `torchaudio`, `scikit-learn`, `pandas`.

## Global Constraints

- Source label CSV: `ml/dysfluency/sep28k-src/SEP-28k_labels.subset4.csv` (columns include `Show, EpId, ClipId, Start, Stop, Unsure, PoorAudioQuality, Prolongation, Block, SoundRep, WordRep, DifficultToUnderstand, Interjection, NoStutteredWords, NaturalPause, Music, NoSpeech`; header has leading spaces after commas — read with `skipinitialspace=True`).
- Clip audio location: `data/sep28k/audio/clips/{Show}/{EpId}/{Show}_{EpId}_{ClipId}.wav`, 16kHz mono, 3 seconds each.
- Five target label columns for the classifier, in this fixed order everywhere: `["Block", "Prolongation", "SoundRep", "WordRep", "Interjection"]`. Binarize as `int(count) > 0`.
- Quality filter columns (drop row if any > 0): `PoorAudioQuality`, `NoSpeech`, `Music`, `Unsure`.
- Split unit is the episode (`(Show, EpId)` pair), not the clip. No episode's clips may appear in more than one of train/val/test. Target ratios ~70/15/15, stratified so all 4 shows appear in every split. Split must be deterministic given a fixed seed (`42`).
- Base model: `facebook/wav2vec2-base` from `transformers`. Freeze the feature extractor (`model.freeze_feature_encoder()`), fine-tune everything else.
- Loss: `BCEWithLogitsLoss` with `pos_weight` computed per class from the **training split only** as `(num_train_negatives / num_train_positives)` per class.
- Local venv: `ml/.venv` (Python 3.12), already has `torch==2.11.0+cu128` installed and GPU-verified (`torch.cuda.is_available() == True` on "NVIDIA GeForce RTX 5050 Laptop GPU"). Do not reinstall torch.
- `ml/requirements.txt`'s `torch==2.5.1` pin and its cloud-only warning comment are stale for M4 (M4 doesn't need bitsandbytes/QLoRA) and must be corrected as part of this plan — the file must reflect what's actually installed and viable.
- All new scripts live under `ml/dysfluency/`, tests under `ml/dysfluency/scripts/tests/`, following the existing `download_sep28k.py` conventions: module docstring explaining purpose/context, `from __future__ import annotations`, `argparse` CLI entry point, `dataclass` for structured records.
- Checkpoints and split manifests are data artifacts, not source — do not commit them (see Task 1 for exact `.gitignore` additions).

---

## File Structure

- `ml/requirements.txt` — modify: fix the stale torch pin/comment, add/confirm M4 package versions.
- `.gitignore` — modify: ignore `ml/dysfluency/checkpoints/`, `ml/dysfluency/reports/*.json` artifacts stay committed (small), splits already covered by existing `data/sep28k/audio/` ignore.
- `ml/dysfluency/scripts/sep28k_manifest.py` — create: shared helpers for reading the label CSV, binarizing labels, applying the quality filter, and resolving clip paths. Used by both `prepare_splits.py` and the tests.
- `ml/dysfluency/scripts/prepare_splits.py` — create: CLI script that reads the label CSV, applies filtering/binarization, performs the episode-stratified split, writes the three manifest CSVs.
- `ml/dysfluency/scripts/tests/test_sep28k_manifest.py` — create: unit tests for the manifest helpers and the split logic.
- `ml/dysfluency/scripts/train_classifier.py` — create: CLI script that loads manifests, builds the `Wav2Vec2ForSequenceClassification` model, trains with weighted BCE, saves the best checkpoint + training log.
- `ml/dysfluency/scripts/evaluate_classifier.py` — create: CLI script that loads a checkpoint and the test manifest, computes per-class F1/precision/recall/PR-AUC, writes a JSON + markdown report.
- `ml/dysfluency/inference.py` — create: minimal wrapper class, no training-loop or CLI code, importable by a future backend.
- `ml/dysfluency/scripts/tests/test_inference.py` — create: unit test for the inference wrapper's output contract (using a tiny untrained model, not the real checkpoint, so it runs fast and without GPU).
- `ml/dysfluency/reports/` — create (empty dir + `.gitkeep`): destination for the committed metrics report.

**Interfaces locked across files:**
- `LABEL_COLUMNS = ["Block", "Prolongation", "SoundRep", "WordRep", "Interjection"]` — defined once in `sep28k_manifest.py`, imported everywhere else that needs it (`train_classifier.py`, `evaluate_classifier.py`, `inference.py`).
- Manifest CSV schema (all three split files): columns `clip_path, Block, SoundRep, ..., Interjection` — `clip_path` is a path relative to the repo root (POSIX-style, `/`-separated), the five label columns are `0`/`1` integers in `LABEL_COLUMNS` order.
- `sep28k_manifest.load_labels(csv_path: Path) -> list[ClipLabel]`, where `ClipLabel` is a dataclass with fields `show: str`, `ep_id: str`, `clip_id: str`, `labels: dict[str, int]` (raw counts, all label + quality columns), `clip_path: Path` (already resolved to the real file location, not yet checked for existence).
- `sep28k_manifest.passes_quality_filter(clip: ClipLabel) -> bool`.
- `sep28k_manifest.binarize(clip: ClipLabel) -> list[int]` — returns 5 ints in `LABEL_COLUMNS` order.
- `sep28k_manifest.split_episodes(clips: list[ClipLabel], seed: int = 42) -> dict[str, list[ClipLabel]]` — returns `{"train": [...], "val": [...], "test": [...]}`.
- `inference.DysfluencyClassifier(checkpoint_dir: str | Path, device: str = "cpu")` — class with `.predict(audio: np.ndarray, sample_rate: int = 16000) -> dict[str, float]`, returning `{"Block": 0.12, "Prolongation": 0.87, ...}` (all 5 `LABEL_COLUMNS` keys, sigmoid probabilities).

---

### Task 1: Fix stale environment pins, add gitignore entries

**Files:**
- Modify: `ml/requirements.txt`
- Modify: `.gitignore`

**Interfaces:** None (config-only task).

- [ ] **Step 1: Update `ml/requirements.txt`**

Replace the top comment and the `torch`/`torchaudio` pins. The QLoRA warning still applies to M7, but must no longer read as a blanket rule against all local GPU training — M4 runs locally and doesn't touch `bitsandbytes`.

```
# Training / evaluation environment (Track M).
#
# M7 (QLoRA fine-tuning) must run on Colab T4 / Kaggle 2xT4 (sm_75), NOT on
# the RTX 5050 -- bitsandbytes has no working Blackwell (sm_120) build, so
# that stage trains in the cloud and only the GGUF conversion runs locally.
# See docs/PROJECT_PLAN.md, "GGUF via llama.cpp, never bitsandbytes".
#
# M4 (wav2vec2 dysfluency classifier) has no bitsandbytes dependency and
# DOES run locally on the RTX 5050 -- verified working with torch 2.11.0+cu128
# (torch.cuda.is_available() == True, "NVIDIA GeForce RTX 5050 Laptop GPU").

# ---- Core ----
torch==2.11.0
transformers==4.57.6
datasets==3.2.0
accelerate==1.2.1

# ---- QLoRA fine-tuning (M7) ----
peft==0.14.0
bitsandbytes==0.45.0
trl==0.13.0

# ---- Dysfluency classifier (M4): wav2vec2 + SEP-28k ----
torchaudio==2.11.0
librosa==0.10.2.post1
soundfile==0.12.1
scikit-learn==1.6.0

# ---- Evaluation (M9, M10, M12) ----
evaluate==0.4.3
rouge-score==0.1.2
bert-score==0.3.13
pandas==2.2.3
matplotlib==3.10.0

# ---- Dataset acquisition (M3) ----
yt-dlp==2024.12.13
requests==2.32.3
tqdm==4.67.1
```

Note: `torch`/`torchaudio` install from the PyTorch cu128 index
(`--index-url https://download.pytorch.org/whl/cu128`), not plain PyPI —
add a one-line comment above the `torch==2.11.0` line saying so, since a
plain `pip install -r requirements.txt` against PyPI would otherwise pull a
CPU-only or CUDA-mismatched wheel.

- [ ] **Step 2: Add checkpoint dir to `.gitignore`**

Add near the existing `ml/finetuning/checkpoints/` line:

```
ml/dysfluency/checkpoints/
```

- [ ] **Step 3: Commit**

```bash
git add ml/requirements.txt .gitignore
git commit -m "chore(m4): correct torch pin and cloud-only note for local GPU training

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 2: Manifest helpers — label loading, quality filter, binarization

**Files:**
- Create: `ml/dysfluency/scripts/sep28k_manifest.py`
- Test: `ml/dysfluency/scripts/tests/test_sep28k_manifest.py`

**Interfaces:**
- Produces: `LABEL_COLUMNS`, `ClipLabel` dataclass, `load_labels`, `passes_quality_filter`, `binarize` (all defined above in File Structure).
- Consumes: nothing from other tasks.

- [ ] **Step 1: Write the failing tests**

Create `ml/dysfluency/scripts/tests/test_sep28k_manifest.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ml/dysfluency/scripts && "../../.venv/Scripts/python.exe" -m pytest tests/test_sep28k_manifest.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sep28k_manifest'`

- [ ] **Step 3: Write the implementation**

Create `ml/dysfluency/scripts/sep28k_manifest.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ml/dysfluency/scripts && "../../.venv/Scripts/python.exe" -m pytest tests/test_sep28k_manifest.py -v`
Expected: PASS, all tests green

- [ ] **Step 5: Add a split-integrity test**

Append to `ml/dysfluency/scripts/tests/test_sep28k_manifest.py`:

```python
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
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd ml/dysfluency/scripts && "../../.venv/Scripts/python.exe" -m pytest tests/test_sep28k_manifest.py -v`
Expected: PASS, all tests green (9 tests total)

- [ ] **Step 7: Commit**

```bash
git add ml/dysfluency/scripts/sep28k_manifest.py ml/dysfluency/scripts/tests/test_sep28k_manifest.py
git commit -m "feat(m4): SEP-28k manifest helpers -- label loading, quality filter, episode split

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 3: `prepare_splits.py` CLI — build train/val/test manifest CSVs

**Files:**
- Create: `ml/dysfluency/scripts/prepare_splits.py`
- Test: covered by Task 2's tests for the underlying logic; this task adds one integration-style test for the CLI's manifest-writing behavior.
- Test: `ml/dysfluency/scripts/tests/test_prepare_splits.py`

**Interfaces:**
- Consumes: `sep28k_manifest.load_labels`, `passes_quality_filter`, `binarize`, `split_episodes`, `LABEL_COLUMNS` (Task 2).
- Produces: `prepare_splits.write_manifest(clips: list[ClipLabel], out_path: Path) -> None`, and a `main()` CLI entry point. Manifest CSV schema is locked in the plan header: `clip_path,Block,Prolongation,SoundRep,WordRep,Interjection`.

- [ ] **Step 1: Write the failing test**

Create `ml/dysfluency/scripts/tests/test_prepare_splits.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ml/dysfluency/scripts && "../../.venv/Scripts/python.exe" -m pytest tests/test_prepare_splits.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'prepare_splits'`

- [ ] **Step 3: Write the implementation**

Create `ml/dysfluency/scripts/prepare_splits.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ml/dysfluency/scripts && "../../.venv/Scripts/python.exe" -m pytest tests/test_prepare_splits.py -v`
Expected: PASS

- [ ] **Step 5: Run the real thing against the actual downloaded dataset**

Run: `cd ml/dysfluency/scripts && "../../.venv/Scripts/python.exe" prepare_splits.py`
Expected: prints loaded/filtered/split counts, writes `data/sep28k/audio/splits/{train,val,test}.csv`. Verify the three files exist and their row counts roughly sum to ~85% of 20,170 (quality filter removes ~15%), with the ~70/15/15 ratio holding.

Run: `wc -l ../../../data/sep28k/audio/splits/*.csv` (or equivalent) and sanity-check the numbers before moving on.

- [ ] **Step 6: Commit**

```bash
git add ml/dysfluency/scripts/prepare_splits.py ml/dysfluency/scripts/tests/test_prepare_splits.py
git commit -m "feat(m4): prepare_splits CLI -- build filtered, episode-split manifests

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

Note: the generated manifest CSVs under `data/sep28k/audio/splits/` are
already covered by the existing `data/sep28k/audio/` `.gitignore` entry --
confirm with `git status` that they show as untracked/ignored, not staged.

---

### Task 4: Install M4 training dependencies into `ml/.venv`

**Files:** None created/modified (environment setup only, verified by a smoke-test script that Task 5 will also rely on).

**Interfaces:** None.

- [ ] **Step 1: Install the packages this task's siblings need, on top of the already-installed torch**

Run (from `ml/`):
```bash
.venv/Scripts/python.exe -m pip install torchaudio==2.11.0 --index-url https://download.pytorch.org/whl/cu128
.venv/Scripts/python.exe -m pip install transformers==4.57.6 datasets==3.2.0 accelerate==1.2.1 scikit-learn==1.6.0 pandas==2.2.3 numpy soundfile==0.12.1 pytest
```

- [ ] **Step 2: Verify the install**

Run:
```bash
.venv/Scripts/python.exe -c "import torch, torchaudio, transformers, sklearn, pandas, soundfile; print(torch.__version__, torchaudio.__version__, transformers.__version__); print('cuda:', torch.cuda.is_available())"
```
Expected: version numbers print, `cuda: True`.

- [ ] **Step 3: No commit needed**

This step only changes the local venv, which is not committed to git (confirm `ml/.venv` is already gitignored via `.gitignore`; if not, that is a pre-existing gap outside this plan's scope -- flag it rather than fixing it silently).

---

### Task 5: `train_classifier.py` — model, weighted loss, training loop

**Files:**
- Create: `ml/dysfluency/scripts/train_classifier.py`
- Test: `ml/dysfluency/scripts/tests/test_train_classifier.py`

**Interfaces:**
- Consumes: `sep28k_manifest.LABEL_COLUMNS` (Task 2), the manifest CSV schema from Task 3, `transformers.Wav2Vec2ForSequenceClassification`, `transformers.Wav2Vec2FeatureExtractor`.
- Produces: `train_classifier.compute_pos_weight(manifest_path: Path) -> torch.Tensor` (length-5, order `LABEL_COLUMNS`), `train_classifier.ClipDataset` (a `torch.utils.data.Dataset` reading manifest rows and returning `(waveform: np.ndarray, labels: torch.FloatTensor)`), and a `main()` CLI entry point that trains and saves a checkpoint directory compatible with `transformers`' `.from_pretrained()` / `.save_pretrained()`.

Because this task's correctness depends on real GPU training behavior that
is impractical to unit-test end-to-end, tests cover only the two
pure-logic pieces (`compute_pos_weight`, `ClipDataset.__getitem__` shape
contract) with a tiny synthetic manifest -- not the training loop itself,
which Step 6 verifies by actually running it.

- [ ] **Step 1: Write the failing tests**

Create `ml/dysfluency/scripts/tests/test_train_classifier.py`:

```python
from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
import torch

from train_classifier import ClipDataset, compute_pos_weight


def _write_manifest(tmp_path: Path, rows: list[dict]) -> Path:
    manifest_path = tmp_path / "manifest.csv"
    with open(manifest_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["clip_path", "Block", "Prolongation", "SoundRep", "WordRep", "Interjection"])
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return manifest_path


def _make_wav(path: Path, seconds: float = 3.0, sr: int = 16000):
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(path, np.zeros(int(seconds * sr), dtype=np.float32), sr)


def test_compute_pos_weight_inverse_frequency(tmp_path):
    rows = [
        {"clip_path": "a.wav", "Block": 1, "Prolongation": 0, "SoundRep": 0, "WordRep": 0, "Interjection": 0},
        {"clip_path": "b.wav", "Block": 1, "Prolongation": 0, "SoundRep": 0, "WordRep": 0, "Interjection": 0},
        {"clip_path": "c.wav", "Block": 0, "Prolongation": 0, "SoundRep": 0, "WordRep": 0, "Interjection": 0},
        {"clip_path": "d.wav", "Block": 0, "Prolongation": 0, "SoundRep": 0, "WordRep": 0, "Interjection": 0},
    ]
    manifest_path = _write_manifest(tmp_path, rows)

    weight = compute_pos_weight(manifest_path)

    assert weight.shape == (5,)
    # Block: 2 positive, 2 negative -> pos_weight = 2/2 = 1.0
    assert weight[0].item() == pytest.approx(1.0)
    # Prolongation: 0 positive -- must not divide by zero; expect weight 1.0 fallback
    assert weight[1].item() == pytest.approx(1.0)


def test_clip_dataset_getitem_shape(tmp_path):
    wav_path = tmp_path / "clips" / "a.wav"
    _make_wav(wav_path)
    rows = [{"clip_path": str(wav_path), "Block": 1, "Prolongation": 0, "SoundRep": 0, "WordRep": 0, "Interjection": 1}]
    manifest_path = _write_manifest(tmp_path, rows)

    dataset = ClipDataset(manifest_path, repo_root=Path("."))

    assert len(dataset) == 1
    waveform, labels = dataset[0]
    assert waveform.ndim == 1
    assert waveform.shape[0] == 16000 * 3
    assert isinstance(labels, torch.Tensor)
    assert labels.shape == (5,)
    assert labels.dtype == torch.float32
    assert labels.tolist() == [1.0, 0.0, 0.0, 0.0, 1.0]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ml/dysfluency/scripts && "../../.venv/Scripts/python.exe" -m pytest tests/test_train_classifier.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'train_classifier'`

- [ ] **Step 3: Write the implementation**

Create `ml/dysfluency/scripts/train_classifier.py`:

```python
"""Fine-tune wav2vec2-base as a multi-label dysfluency classifier.

Freezes the CNN feature extractor, fine-tunes the transformer encoder plus
a 5-way sigmoid classification head, using BCEWithLogitsLoss with a
per-class pos_weight computed from the training split to counter class
imbalance. Saves the checkpoint with the best validation macro-F1.

See docs/superpowers/specs/2026-09-06-m4-dysfluency-classifier-design.md.
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

import numpy as np
import soundfile as sf
import torch
from sklearn.metrics import f1_score
from torch.utils.data import DataLoader, Dataset
from transformers import Wav2Vec2FeatureExtractor, Wav2Vec2ForSequenceClassification

from sep28k_manifest import LABEL_COLUMNS

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SPLITS_DIR = REPO_ROOT / "data" / "sep28k" / "audio" / "splits"
DEFAULT_CHECKPOINT_DIR = REPO_ROOT / "ml" / "dysfluency" / "checkpoints" / "wav2vec2-dysfluency"
MODEL_NAME = "facebook/wav2vec2-base"
SAMPLE_RATE = 16000
CLIP_SECONDS = 3
EXPECTED_SAMPLES = SAMPLE_RATE * CLIP_SECONDS


def compute_pos_weight(manifest_path: Path) -> torch.Tensor:
    """Inverse-frequency pos_weight per class: negatives / positives.

    Falls back to 1.0 for a class with zero positives in this manifest
    (division by zero would otherwise propagate NaN into the loss).
    """
    counts = {col: 0 for col in LABEL_COLUMNS}
    total = 0
    with open(manifest_path, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            total += 1
            for col in LABEL_COLUMNS:
                counts[col] += int(row[col])

    weights = []
    for col in LABEL_COLUMNS:
        pos = counts[col]
        neg = total - pos
        weights.append(neg / pos if pos > 0 else 1.0)
    return torch.tensor(weights, dtype=torch.float32)


class ClipDataset(Dataset):
    """Reads a manifest CSV, loads each clip's raw waveform on access."""

    def __init__(self, manifest_path: Path, repo_root: Path = REPO_ROOT):
        self.repo_root = repo_root
        with open(manifest_path, encoding="utf-8", newline="") as f:
            self.rows = list(csv.DictReader(f))

    def __len__(self) -> int:
        return len(self.rows)

    def __getitem__(self, idx: int) -> tuple[np.ndarray, torch.Tensor]:
        row = self.rows[idx]
        clip_path = Path(row["clip_path"])
        if not clip_path.is_absolute():
            clip_path = self.repo_root / clip_path
        waveform, sr = sf.read(clip_path, dtype="float32")
        if sr != SAMPLE_RATE:
            raise ValueError(f"{clip_path}: expected {SAMPLE_RATE}Hz, got {sr}Hz")
        if waveform.ndim > 1:
            waveform = waveform.mean(axis=1)
        if len(waveform) < EXPECTED_SAMPLES:
            waveform = np.pad(waveform, (0, EXPECTED_SAMPLES - len(waveform)))
        elif len(waveform) > EXPECTED_SAMPLES:
            waveform = waveform[:EXPECTED_SAMPLES]
        labels = torch.tensor([float(row[col]) for col in LABEL_COLUMNS], dtype=torch.float32)
        return waveform, labels


def make_collate_fn(feature_extractor: Wav2Vec2FeatureExtractor):
    def collate(batch: list[tuple[np.ndarray, torch.Tensor]]):
        waveforms = [item[0] for item in batch]
        labels = torch.stack([item[1] for item in batch])
        inputs = feature_extractor(
            waveforms, sampling_rate=SAMPLE_RATE, return_tensors="pt", padding=True
        )
        return inputs.input_values, labels

    return collate


def evaluate(model, loader, device) -> float:
    """Returns validation macro-F1 at a 0.5 threshold."""
    model.eval()
    all_preds, all_labels = [], []
    with torch.no_grad():
        for input_values, labels in loader:
            input_values = input_values.to(device)
            logits = model(input_values).logits
            preds = (torch.sigmoid(logits) > 0.5).int().cpu().numpy()
            all_preds.append(preds)
            all_labels.append(labels.int().numpy())
    all_preds = np.concatenate(all_preds)
    all_labels = np.concatenate(all_labels)
    return f1_score(all_labels, all_preds, average="macro", zero_division=0)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--splits-dir", type=Path, default=DEFAULT_SPLITS_DIR)
    parser.add_argument("--checkpoint-dir", type=Path, default=DEFAULT_CHECKPOINT_DIR)
    parser.add_argument("--epochs", type=int, default=5)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--lr", type=float, default=3e-5)
    args = parser.parse_args()

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Training on device: {device}")

    train_manifest = args.splits_dir / "train.csv"
    val_manifest = args.splits_dir / "val.csv"

    feature_extractor = Wav2Vec2FeatureExtractor.from_pretrained(MODEL_NAME)
    train_dataset = ClipDataset(train_manifest)
    val_dataset = ClipDataset(val_manifest)
    collate_fn = make_collate_fn(feature_extractor)
    train_loader = DataLoader(train_dataset, batch_size=args.batch_size, shuffle=True, collate_fn=collate_fn)
    val_loader = DataLoader(val_dataset, batch_size=args.batch_size, shuffle=False, collate_fn=collate_fn)

    model = Wav2Vec2ForSequenceClassification.from_pretrained(
        MODEL_NAME,
        num_labels=len(LABEL_COLUMNS),
        problem_type="multi_label_classification",
    )
    model.freeze_feature_encoder()
    model.to(device)

    pos_weight = compute_pos_weight(train_manifest).to(device)
    loss_fn = torch.nn.BCEWithLogitsLoss(pos_weight=pos_weight)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr)

    best_val_f1 = -1.0
    for epoch in range(args.epochs):
        model.train()
        total_loss = 0.0
        for input_values, labels in train_loader:
            input_values, labels = input_values.to(device), labels.to(device)
            optimizer.zero_grad()
            logits = model(input_values).logits
            loss = loss_fn(logits, labels)
            loss.backward()
            optimizer.step()
            total_loss += loss.item()

        val_f1 = evaluate(model, val_loader, device)
        print(f"Epoch {epoch + 1}/{args.epochs}: train_loss={total_loss / len(train_loader):.4f} val_macro_f1={val_f1:.4f}")

        if val_f1 > best_val_f1:
            best_val_f1 = val_f1
            args.checkpoint_dir.mkdir(parents=True, exist_ok=True)
            model.save_pretrained(args.checkpoint_dir)
            feature_extractor.save_pretrained(args.checkpoint_dir)
            print(f"  Saved new best checkpoint (val_macro_f1={val_f1:.4f}) to {args.checkpoint_dir}")

    print(f"Training complete. Best val_macro_f1={best_val_f1:.4f}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ml/dysfluency/scripts && "../../.venv/Scripts/python.exe" -m pytest tests/test_train_classifier.py -v`
Expected: PASS

- [ ] **Step 5: Commit the code (before the real training run)**

```bash
git add ml/dysfluency/scripts/train_classifier.py ml/dysfluency/scripts/tests/test_train_classifier.py
git commit -m "feat(m4): train_classifier -- wav2vec2 multi-label fine-tuning with weighted BCE

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

- [ ] **Step 6: Run the real training job on the RTX 5050**

Run: `cd ml/dysfluency/scripts && "../../.venv/Scripts/python.exe" train_classifier.py --epochs 5 --batch-size 8`

This downloads `facebook/wav2vec2-base` from the HuggingFace Hub on first
run (~360MB) and trains for up to 5 epochs. Expected: GPU memory usage
stays under 8GB (watch with `nvidia-smi` in a second terminal if unsure);
per-epoch `val_macro_f1` printed and generally trending upward; a checkpoint
directory appears at `ml/dysfluency/checkpoints/wav2vec2-dysfluency/`
containing `config.json`, `model.safetensors` (or `pytorch_model.bin`), and
the feature extractor's `preprocessor_config.json`.

If CUDA runs out of memory, reduce `--batch-size` to 4 and retry -- do not
silently fall back to CPU without telling the user first.

No commit needed for this step (the checkpoint is gitignored per Task 1).

---

### Task 6: `evaluate_classifier.py` — per-class F1 report

**Files:**
- Create: `ml/dysfluency/scripts/evaluate_classifier.py`
- Test: `ml/dysfluency/scripts/tests/test_evaluate_classifier.py`

**Interfaces:**
- Consumes: `sep28k_manifest.LABEL_COLUMNS` (Task 2), `train_classifier.ClipDataset`, `make_collate_fn` (Task 5), the checkpoint directory produced by Task 5 Step 6.
- Produces: `evaluate_classifier.compute_metrics(y_true: np.ndarray, y_pred: np.ndarray, y_prob: np.ndarray) -> dict` returning per-class `{"f1", "precision", "recall", "pr_auc"}` keyed by `LABEL_COLUMNS` entries, plus a `"macro_f1"` top-level key. A `main()` CLI entry point that writes `ml/dysfluency/reports/metrics.json` and `ml/dysfluency/reports/metrics.md`.

- [ ] **Step 1: Write the failing test**

Create `ml/dysfluency/scripts/tests/test_evaluate_classifier.py`:

```python
from __future__ import annotations

import numpy as np

from evaluate_classifier import compute_metrics


def test_compute_metrics_perfect_predictions():
    y_true = np.array([[1, 0], [0, 1], [1, 1]])
    y_pred = y_true.copy()
    y_prob = y_true.astype(float)

    metrics = compute_metrics(y_true, y_pred, y_prob, label_names=["A", "B"])

    assert metrics["A"]["f1"] == 1.0
    assert metrics["A"]["precision"] == 1.0
    assert metrics["A"]["recall"] == 1.0
    assert metrics["B"]["f1"] == 1.0
    assert metrics["macro_f1"] == 1.0


def test_compute_metrics_all_wrong():
    y_true = np.array([[1, 0], [0, 1]])
    y_pred = np.array([[0, 1], [1, 0]])
    y_prob = y_pred.astype(float)

    metrics = compute_metrics(y_true, y_pred, y_prob, label_names=["A", "B"])

    assert metrics["A"]["f1"] == 0.0
    assert metrics["B"]["f1"] == 0.0


def test_compute_metrics_includes_pr_auc_key():
    y_true = np.array([[1], [0], [1], [0]])
    y_pred = np.array([[1], [0], [0], [0]])
    y_prob = np.array([[0.9], [0.2], [0.4], [0.1]])

    metrics = compute_metrics(y_true, y_pred, y_prob, label_names=["A"])

    assert "pr_auc" in metrics["A"]
    assert 0.0 <= metrics["A"]["pr_auc"] <= 1.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ml/dysfluency/scripts && "../../.venv/Scripts/python.exe" -m pytest tests/test_evaluate_classifier.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'evaluate_classifier'`

- [ ] **Step 3: Write the implementation**

Create `ml/dysfluency/scripts/evaluate_classifier.py`:

```python
"""Evaluate a trained dysfluency classifier checkpoint on the held-out test split.

Reports per-class F1, precision, recall, and PR-AUC (threshold-independent),
plus a single macro-F1 summary number -- the M4 deliverable is explicitly
"per-class F1", reported here alongside precision/recall/PR-AUC for context.

See docs/superpowers/specs/2026-09-06-m4-dysfluency-classifier-design.md.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import torch
from sklearn.metrics import average_precision_score, f1_score, precision_score, recall_score
from torch.utils.data import DataLoader
from transformers import Wav2Vec2FeatureExtractor, Wav2Vec2ForSequenceClassification

from sep28k_manifest import LABEL_COLUMNS
from train_classifier import ClipDataset, make_collate_fn

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CHECKPOINT_DIR = REPO_ROOT / "ml" / "dysfluency" / "checkpoints" / "wav2vec2-dysfluency"
DEFAULT_TEST_MANIFEST = REPO_ROOT / "data" / "sep28k" / "audio" / "splits" / "test.csv"
DEFAULT_REPORTS_DIR = REPO_ROOT / "ml" / "dysfluency" / "reports"


def compute_metrics(
    y_true: np.ndarray, y_pred: np.ndarray, y_prob: np.ndarray, label_names: list[str]
) -> dict:
    metrics: dict = {}
    for i, name in enumerate(label_names):
        metrics[name] = {
            "f1": float(f1_score(y_true[:, i], y_pred[:, i], zero_division=0)),
            "precision": float(precision_score(y_true[:, i], y_pred[:, i], zero_division=0)),
            "recall": float(recall_score(y_true[:, i], y_pred[:, i], zero_division=0)),
            "pr_auc": float(average_precision_score(y_true[:, i], y_prob[:, i])),
        }
    metrics["macro_f1"] = float(f1_score(y_true, y_pred, average="macro", zero_division=0))
    return metrics


def write_markdown_report(metrics: dict, label_names: list[str], out_path: Path) -> None:
    lines = ["| Class | F1 | Precision | Recall | PR-AUC |", "|---|---|---|---|---|"]
    for name in label_names:
        m = metrics[name]
        lines.append(f"| {name} | {m['f1']:.3f} | {m['precision']:.3f} | {m['recall']:.3f} | {m['pr_auc']:.3f} |")
    lines.append(f"\n**Macro F1:** {metrics['macro_f1']:.3f}")
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint-dir", type=Path, default=DEFAULT_CHECKPOINT_DIR)
    parser.add_argument("--test-manifest", type=Path, default=DEFAULT_TEST_MANIFEST)
    parser.add_argument("--reports-dir", type=Path, default=DEFAULT_REPORTS_DIR)
    parser.add_argument("--batch-size", type=int, default=8)
    args = parser.parse_args()

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    feature_extractor = Wav2Vec2FeatureExtractor.from_pretrained(args.checkpoint_dir)
    model = Wav2Vec2ForSequenceClassification.from_pretrained(args.checkpoint_dir)
    model.to(device)
    model.eval()

    dataset = ClipDataset(args.test_manifest)
    loader = DataLoader(dataset, batch_size=args.batch_size, shuffle=False, collate_fn=make_collate_fn(feature_extractor))

    all_probs, all_labels = [], []
    with torch.no_grad():
        for input_values, labels in loader:
            logits = model(input_values.to(device)).logits
            all_probs.append(torch.sigmoid(logits).cpu().numpy())
            all_labels.append(labels.numpy())

    y_prob = np.concatenate(all_probs)
    y_true = np.concatenate(all_labels).astype(int)
    y_pred = (y_prob > 0.5).astype(int)

    metrics = compute_metrics(y_true, y_pred, y_prob, LABEL_COLUMNS)

    args.reports_dir.mkdir(parents=True, exist_ok=True)
    (args.reports_dir / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    write_markdown_report(metrics, LABEL_COLUMNS, args.reports_dir / "metrics.md")

    print(json.dumps(metrics, indent=2))
    print(f"\nReports written to {args.reports_dir}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ml/dysfluency/scripts && "../../.venv/Scripts/python.exe" -m pytest tests/test_evaluate_classifier.py -v`
Expected: PASS

- [ ] **Step 5: Create the reports directory placeholder and commit the code**

```bash
mkdir -p ml/dysfluency/reports
touch ml/dysfluency/reports/.gitkeep
git add ml/dysfluency/scripts/evaluate_classifier.py ml/dysfluency/scripts/tests/test_evaluate_classifier.py ml/dysfluency/reports/.gitkeep
git commit -m "feat(m4): evaluate_classifier -- per-class F1/precision/recall/PR-AUC report

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

- [ ] **Step 6: Run the real evaluation against Task 5's trained checkpoint**

Run: `cd ml/dysfluency/scripts && "../../.venv/Scripts/python.exe" evaluate_classifier.py`

Expected: prints the metrics JSON, writes `ml/dysfluency/reports/metrics.json`
and `ml/dysfluency/reports/metrics.md`. Read `metrics.md` and sanity-check:
all 5 classes present, F1 scores between 0 and 1 and not all zero (a
model that never fires or always fires on every class would indicate a
training bug, not a reporting bug -- if that happens, stop and investigate
before committing the report).

- [ ] **Step 7: Commit the metrics report**

```bash
git add ml/dysfluency/reports/metrics.json ml/dysfluency/reports/metrics.md
git commit -m "docs(m4): record trained classifier's per-class F1 results

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 7: `inference.py` — minimal wrapper for future backend use

**Files:**
- Create: `ml/dysfluency/inference.py`
- Test: `ml/dysfluency/scripts/tests/test_inference.py`

**Interfaces:**
- Consumes: `sep28k_manifest.LABEL_COLUMNS` (Task 2), a checkpoint directory in the format Task 5 produces (any `transformers`-compatible `Wav2Vec2ForSequenceClassification` checkpoint works, including a tiny untrained one for testing).
- Produces: `DysfluencyClassifier` class (constructor and `.predict()` signature locked in the plan header's File Structure section). This is the only file in `ml/dysfluency/` intended for import by other subsystems (e.g. a future backend task) -- it must not import `argparse`, training-loop code, or anything from `ml/dysfluency/scripts/`.

- [ ] **Step 1: Write the failing test**

Create `ml/dysfluency/scripts/tests/test_inference.py`:

```python
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from transformers import Wav2Vec2Config, Wav2Vec2FeatureExtractor, Wav2Vec2ForSequenceClassification

from inference import DysfluencyClassifier


@pytest.fixture
def tiny_checkpoint(tmp_path: Path) -> Path:
    """A small, untrained wav2vec2 checkpoint -- fast to build, no network
    access, only used to exercise the inference wrapper's plumbing."""
    config = Wav2Vec2Config(
        hidden_size=32,
        num_hidden_layers=2,
        num_attention_heads=2,
        intermediate_size=64,
        conv_dim=(32, 32),
        conv_stride=(5, 2),
        conv_kernel=(10, 3),
        num_labels=5,
        problem_type="multi_label_classification",
    )
    model = Wav2Vec2ForSequenceClassification(config)
    checkpoint_dir = tmp_path / "tiny-checkpoint"
    model.save_pretrained(checkpoint_dir)

    feature_extractor = Wav2Vec2FeatureExtractor(
        feature_size=1, sampling_rate=16000, padding_value=0.0, do_normalize=True, return_attention_mask=False
    )
    feature_extractor.save_pretrained(checkpoint_dir)
    return checkpoint_dir


def test_predict_returns_all_label_keys_with_valid_probabilities(tiny_checkpoint):
    classifier = DysfluencyClassifier(tiny_checkpoint, device="cpu")
    audio = np.zeros(16000 * 3, dtype=np.float32)

    result = classifier.predict(audio, sample_rate=16000)

    assert set(result.keys()) == {"Block", "Prolongation", "SoundRep", "WordRep", "Interjection"}
    for prob in result.values():
        assert 0.0 <= prob <= 1.0


def test_predict_accepts_variable_length_audio(tiny_checkpoint):
    classifier = DysfluencyClassifier(tiny_checkpoint, device="cpu")
    short_audio = np.zeros(8000, dtype=np.float32)

    result = classifier.predict(short_audio, sample_rate=16000)

    assert len(result) == 5
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ml/dysfluency/scripts && "../../.venv/Scripts/python.exe" -m pytest tests/test_inference.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'inference'`

- [ ] **Step 3: Write the implementation**

Create `ml/dysfluency/inference.py`:

```python
"""Minimal inference wrapper for the trained M4 dysfluency classifier.

Intended for import by a future backend integration (the Grounded
Knowledge Mode cascade's "Dysfluency analyzer" stage) -- deliberately has
no training-loop, CLI, or dataset-manifest code so it stays a small,
stable surface to depend on.

See docs/superpowers/specs/2026-09-06-m4-dysfluency-classifier-design.md.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import torch
from transformers import Wav2Vec2FeatureExtractor, Wav2Vec2ForSequenceClassification

LABEL_COLUMNS = ["Block", "Prolongation", "SoundRep", "WordRep", "Interjection"]


class DysfluencyClassifier:
    def __init__(self, checkpoint_dir: str | Path, device: str = "cpu"):
        self.device = torch.device(device)
        self.feature_extractor = Wav2Vec2FeatureExtractor.from_pretrained(checkpoint_dir)
        self.model = Wav2Vec2ForSequenceClassification.from_pretrained(checkpoint_dir)
        self.model.to(self.device)
        self.model.eval()

    def predict(self, audio: np.ndarray, sample_rate: int = 16000) -> dict[str, float]:
        if sample_rate != self.feature_extractor.sampling_rate:
            raise ValueError(
                f"Expected {self.feature_extractor.sampling_rate}Hz audio, got {sample_rate}Hz. "
                "Resample before calling predict()."
            )
        inputs = self.feature_extractor([audio], sampling_rate=sample_rate, return_tensors="pt", padding=True)
        with torch.no_grad():
            logits = self.model(inputs.input_values.to(self.device)).logits
        probs = torch.sigmoid(logits).squeeze(0).cpu().tolist()
        return dict(zip(LABEL_COLUMNS, probs))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ml/dysfluency/scripts && "../../.venv/Scripts/python.exe" -m pytest tests/test_inference.py -v`
Expected: PASS

- [ ] **Step 5: Manually verify against the real trained checkpoint**

Run:
```bash
cd ml/dysfluency
"../.venv/Scripts/python.exe" -c "
from pathlib import Path
import numpy as np
from inference import DysfluencyClassifier

clf = DysfluencyClassifier(Path('checkpoints/wav2vec2-dysfluency'), device='cpu')
audio = np.zeros(16000 * 3, dtype=np.float32)
print(clf.predict(audio))
"
```
Expected: a dict with all 5 label names and float probabilities, no errors.

- [ ] **Step 6: Commit**

```bash
git add ml/dysfluency/inference.py ml/dysfluency/scripts/tests/test_inference.py
git commit -m "feat(m4): inference wrapper for the trained dysfluency classifier

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 8: Update PROJECT_PLAN.md to mark M4 complete

**Files:**
- Modify: `docs/PROJECT_PLAN.md` (the M4 row in the milestones table, around line 177)

**Interfaces:** None (docs-only task).

- [ ] **Step 1: Read the actual results before writing the summary**

Read `ml/dysfluency/reports/metrics.md` (produced in Task 6) and use its
real numbers -- do not write placeholder or estimated F1 values.

- [ ] **Step 2: Update the M4 row**

Edit the M4 row in `docs/PROJECT_PLAN.md` (currently: `| M4 | Train the
dysfluency classifier: wav2vec2-base + multi-label head (block,
prolongation, sound-rep, word-rep, interjection). Report per-class F1. |
Acoustic analyzer |`) to mark it done, following the same style as the M3
row immediately above it (✅ **DONE (date).** summary sentence with real
numbers, mention of the harness/checkpoint location).

- [ ] **Step 3: Commit**

```bash
git add docs/PROJECT_PLAN.md
git commit -m "docs: mark M4 complete with per-class F1 results

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Post-plan: PR

Once all 8 tasks are done and committed on `m4-dysfluency-classifier`, push
the branch and open a PR into `master`, the same way M3 was integrated
(this repo has a remote at `github.com/Ali-Khamis45/we-s2s-finalproject`
and no `gh` CLI available in this environment -- give the user the
`/pull/new/<branch>` link rather than trying to merge directly).
