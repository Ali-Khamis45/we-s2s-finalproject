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
