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
