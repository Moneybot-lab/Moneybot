from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List

import numpy as np
import pandas as pd


FEATURE_COLUMNS = [
    "return_1d",
    "return_5d",
    "rsi_14",
    "macd_hist",
    "vol_ratio_20d",
]


@dataclass
class BaselineModelArtifact:
    version: str
    feature_columns: List[str]
    means: List[float]
    stds: List[float]
    weights: List[float]
    bias: float
    decision_threshold: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "feature_columns": self.feature_columns,
            "means": self.means,
            "stds": self.stds,
            "weights": self.weights,
            "bias": self.bias,
            "decision_threshold": self.decision_threshold,
        }



def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def _rsi_14(close: pd.Series) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(14).mean()
    avg_loss = loss.rolling(14).mean().replace(0, np.nan)
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def engineer_features(price_df: pd.DataFrame) -> pd.DataFrame:
    """Create deterministic feature columns from OHLCV history."""
    required = {"Close", "Volume"}
    missing = required.difference(price_df.columns)
    if missing:
        raise ValueError(f"Missing required columns: {sorted(missing)}")

    out = price_df.copy()
    close = out["Close"].astype(float)
    volume = out["Volume"].astype(float)

    out["return_1d"] = close.pct_change(1)
    out["return_5d"] = close.pct_change(5)
    out["rsi_14"] = _rsi_14(close)

    ema12 = _ema(close, span=12)
    ema26 = _ema(close, span=26)
    macd_line = ema12 - ema26
    signal_line = _ema(macd_line, span=9)
    out["macd_hist"] = macd_line - signal_line

    vol20 = volume.rolling(20).mean().replace(0, np.nan)
    out["vol_ratio_20d"] = volume / vol20

    return out


def attach_labels(feature_df: pd.DataFrame, horizon_days: int = 5, target_return: float = 0.0) -> pd.DataFrame:
    """Add forward-return and binary label columns for supervised training."""
    if "Close" not in feature_df.columns:
        raise ValueError("Close column is required to compute labels")
    out = feature_df.copy()
    forward_close = out["Close"].shift(-horizon_days)
    out[f"forward_return_{horizon_days}d"] = (forward_close / out["Close"]) - 1.0
    out[f"label_up_{horizon_days}d"] = (out[f"forward_return_{horizon_days}d"] > target_return).astype(float)
    return out


def build_training_matrix(labeled_df: pd.DataFrame, horizon_days: int = 5) -> tuple[np.ndarray, np.ndarray, pd.DataFrame]:
    label_col = f"label_up_{horizon_days}d"
    cols = FEATURE_COLUMNS + [label_col]
    frame = labeled_df[cols].replace([np.inf, -np.inf], np.nan).dropna().copy()
    if frame.empty:
        raise ValueError("No rows available after dropping NaN feature/label values")

    X = frame[FEATURE_COLUMNS].to_numpy(dtype=float)
    y = frame[label_col].to_numpy(dtype=float)
    return X, y, frame


def _sigmoid(z: np.ndarray) -> np.ndarray:
    z = np.clip(z, -35.0, 35.0)
    return 1.0 / (1.0 + np.exp(-z))


def train_logistic_baseline(
    X: np.ndarray,
    y: np.ndarray,
    *,
    learning_rate: float = 0.1,
    epochs: int = 400,
    l2: float = 1e-3,
    decision_threshold: float = 0.55,
) -> BaselineModelArtifact:
    """Train deterministic logistic regression using full-batch gradient descent."""
    if X.ndim != 2:
        raise ValueError("X must be a 2D array")
    if y.ndim != 1:
        raise ValueError("y must be a 1D array")
    if X.shape[0] != y.shape[0]:
        raise ValueError("X and y must have matching rows")

    means = X.mean(axis=0)
    stds = X.std(axis=0)
    stds = np.where(stds == 0.0, 1.0, stds)

    Xn = (X - means) / stds
    weights = np.zeros(Xn.shape[1], dtype=float)
    bias = 0.0

    n = float(Xn.shape[0])
    for _ in range(epochs):
        logits = (Xn @ weights) + bias
        preds = _sigmoid(logits)
        error = preds - y

        grad_w = (Xn.T @ error) / n + (l2 * weights)
        grad_b = float(error.mean())

        weights -= learning_rate * grad_w
        bias -= learning_rate * grad_b

    return BaselineModelArtifact(
        version="day1-logreg-v1",
        feature_columns=list(FEATURE_COLUMNS),
        means=means.tolist(),
        stds=stds.tolist(),
        weights=weights.tolist(),
        bias=float(bias),
        decision_threshold=float(decision_threshold),
    )


def predict_proba(artifact: BaselineModelArtifact, rows: np.ndarray) -> np.ndarray:
    if rows.ndim == 1:
        rows = rows.reshape(1, -1)
    means = np.asarray(artifact.means, dtype=float)
    stds = np.asarray(artifact.stds, dtype=float)
    weights = np.asarray(artifact.weights, dtype=float)
    rows_n = (rows - means) / stds
    logits = rows_n @ weights + float(artifact.bias)
    return _sigmoid(logits)


def classify(artifact: BaselineModelArtifact, rows: np.ndarray) -> np.ndarray:
    probs = predict_proba(artifact, rows)
    return (probs >= artifact.decision_threshold).astype(int)


def save_artifact(artifact: BaselineModelArtifact, output_path: str | Path) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(artifact.to_dict(), indent=2), encoding="utf-8")


def load_artifact(path: str | Path) -> BaselineModelArtifact:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return BaselineModelArtifact(
        version=str(payload["version"]),
        feature_columns=list(payload["feature_columns"]),
        means=[float(v) for v in payload["means"]],
        stds=[float(v) for v in payload["stds"]],
        weights=[float(v) for v in payload["weights"]],
        bias=float(payload["bias"]),
        decision_threshold=float(payload.get("decision_threshold", 0.55)),
    )


def chronological_split(df: pd.DataFrame, train_ratio: float = 0.8) -> tuple[pd.DataFrame, pd.DataFrame]:
    if not 0.1 <= train_ratio <= 0.95:
        raise ValueError("train_ratio must be between 0.1 and 0.95")
    pivot = int(len(df) * train_ratio)
    if pivot <= 0 or pivot >= len(df):
        raise ValueError("train_ratio creates an empty train or test split")
    return df.iloc[:pivot].copy(), df.iloc[pivot:].copy()


def summarize_binary_predictions(y_true: Iterable[float], y_pred: Iterable[int]) -> Dict[str, float]:
    yt = np.asarray(list(y_true), dtype=int)
    yp = np.asarray(list(y_pred), dtype=int)
    if yt.shape != yp.shape:
        raise ValueError("y_true and y_pred must be same shape")
    accuracy = float((yt == yp).mean()) if len(yt) else 0.0
    positive_rate = float(yp.mean()) if len(yp) else 0.0
    return {"accuracy": round(accuracy, 4), "positive_rate": round(positive_rate, 4), "rows": float(len(yt))}
