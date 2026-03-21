import numpy as np
import pandas as pd

from moneybot.services.deterministic_model import (
    FEATURE_COLUMNS,
    attach_labels,
    build_training_matrix,
    classify,
    engineer_features,
    predict_proba,
    train_logistic_baseline,
)


def _price_frame(rows: int = 80) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=rows, freq="D")
    close = 115 + (np.sin(np.arange(rows) / 3) * 6) + (np.arange(rows) * 0.12)
    volume = np.linspace(1_000_000, 1_300_000, rows)
    return pd.DataFrame({"Close": close, "Volume": volume}, index=idx)


def test_engineer_features_creates_expected_columns():
    df = _price_frame(90)
    out = engineer_features(df)

    for col in FEATURE_COLUMNS:
        assert col in out.columns

    assert out["rsi_14"].dropna().between(0, 100).all()


def test_attach_labels_and_training_matrix_produce_rows():
    df = _price_frame(100)
    features = engineer_features(df)
    labeled = attach_labels(features, horizon_days=5, target_return=0.0)

    assert "forward_return_5d" in labeled.columns
    assert "label_up_5d" in labeled.columns

    X, y, frame = build_training_matrix(labeled, horizon_days=5)
    assert X.shape[0] == y.shape[0] == len(frame)
    assert X.shape[1] == len(FEATURE_COLUMNS)
    assert set(np.unique(y)).issubset({0.0, 1.0})


def test_train_logistic_baseline_is_deterministic_and_predicts_probabilities():
    df = _price_frame(110)
    labeled = attach_labels(engineer_features(df), horizon_days=5)
    X, y, _ = build_training_matrix(labeled, horizon_days=5)

    model_a = train_logistic_baseline(X, y, epochs=200)
    model_b = train_logistic_baseline(X, y, epochs=200)

    assert model_a.weights == model_b.weights
    assert model_a.bias == model_b.bias

    probs = predict_proba(model_a, X[:5])
    classes = classify(model_a, X[:5])

    assert probs.shape == (5,)
    assert classes.shape == (5,)
    assert np.all((probs >= 0) & (probs <= 1))
    assert set(np.unique(classes)).issubset({0, 1})
