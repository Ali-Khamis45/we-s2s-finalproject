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

from sep28k_manifest import DYSFLUENCY_KIND_BY_LABEL_COLUMN, LABEL_COLUMNS

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

    id2label = {i: DYSFLUENCY_KIND_BY_LABEL_COLUMN[col] for i, col in enumerate(LABEL_COLUMNS)}
    label2id = {v: k for k, v in id2label.items()}
    model = Wav2Vec2ForSequenceClassification.from_pretrained(
        MODEL_NAME,
        num_labels=len(LABEL_COLUMNS),
        problem_type="multi_label_classification",
        id2label=id2label,
        label2id=label2id,
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
