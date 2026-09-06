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
