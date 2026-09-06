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
