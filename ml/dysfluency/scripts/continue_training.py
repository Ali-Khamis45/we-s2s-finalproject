"""One-off continuation run for M4: resume fine-tuning from the saved
checkpoint (epochs 1-3 already completed by train_classifier.py) for
additional epochs, keeping the same loss/optimizer setup.

Not part of the locked plan interfaces (train_classifier.py, evaluate_classifier.py,
inference.py) -- this is a scratch script for the one-time same-day decision
to extend training past the original 3-epoch stopping point. Safe to delete
after use.

Usage:
    python continue_training.py --extra-epochs 2
"""
from __future__ import annotations

import argparse
from pathlib import Path

import torch
from torch.utils.data import DataLoader
from transformers import Wav2Vec2FeatureExtractor, Wav2Vec2ForSequenceClassification

from sep28k_manifest import LABEL_COLUMNS
from train_classifier import (
    DEFAULT_CHECKPOINT_DIR,
    DEFAULT_SPLITS_DIR,
    ClipDataset,
    compute_pos_weight,
    evaluate,
    make_collate_fn,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint-dir", type=Path, default=DEFAULT_CHECKPOINT_DIR)
    parser.add_argument("--splits-dir", type=Path, default=DEFAULT_SPLITS_DIR)
    parser.add_argument("--extra-epochs", type=int, default=2)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--lr", type=float, default=3e-5)
    args = parser.parse_args()

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Resuming from {args.checkpoint_dir} on device: {device}", flush=True)

    train_manifest = args.splits_dir / "train.csv"
    val_manifest = args.splits_dir / "val.csv"

    feature_extractor = Wav2Vec2FeatureExtractor.from_pretrained(args.checkpoint_dir)
    train_dataset = ClipDataset(train_manifest)
    val_dataset = ClipDataset(val_manifest)
    collate_fn = make_collate_fn(feature_extractor)
    train_loader = DataLoader(train_dataset, batch_size=args.batch_size, shuffle=True, collate_fn=collate_fn)
    val_loader = DataLoader(val_dataset, batch_size=args.batch_size, shuffle=False, collate_fn=collate_fn)

    model = Wav2Vec2ForSequenceClassification.from_pretrained(args.checkpoint_dir)
    model.freeze_feature_encoder()
    model.to(device)

    # Baseline: what the resumed checkpoint scores before any further training,
    # so we can tell whether continuing actually helps.
    baseline_f1 = evaluate(model, val_loader, device)
    print(f"Baseline (epoch 3 checkpoint) val_macro_f1={baseline_f1:.4f}", flush=True)

    pos_weight = compute_pos_weight(train_manifest).to(device)
    loss_fn = torch.nn.BCEWithLogitsLoss(pos_weight=pos_weight)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr)

    best_val_f1 = baseline_f1
    for epoch in range(args.extra_epochs):
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
        epoch_num = 3 + epoch + 1
        print(
            f"Epoch {epoch_num} (extra {epoch + 1}/{args.extra_epochs}): "
            f"train_loss={total_loss / len(train_loader):.4f} val_macro_f1={val_f1:.4f}",
            flush=True,
        )

        if val_f1 > best_val_f1:
            best_val_f1 = val_f1
            args.checkpoint_dir.mkdir(parents=True, exist_ok=True)
            model.save_pretrained(args.checkpoint_dir)
            feature_extractor.save_pretrained(args.checkpoint_dir)
            print(f"  Saved new best checkpoint (val_macro_f1={val_f1:.4f}) to {args.checkpoint_dir}", flush=True)
        else:
            print(f"  No improvement over best ({best_val_f1:.4f}), checkpoint not updated", flush=True)

    print(f"Continuation complete. Best val_macro_f1={best_val_f1:.4f} (started from {baseline_f1:.4f})", flush=True)


if __name__ == "__main__":
    main()
