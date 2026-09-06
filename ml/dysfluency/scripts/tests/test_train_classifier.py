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
