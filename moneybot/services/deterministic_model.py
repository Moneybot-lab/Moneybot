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
    "news_sentiment_score",
    "news_headline_count_24h",
    "news_source_score_72h",
    "news_momentum_24h",
    "news_momentum_72h",
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
    calibration_slope: float = 1.0
    calibration_intercept: float = 0.0
    lineage: Dict[str, Any] | None = None
    forecast_horizon: str | None = None
    scaler_type: str = "legacy_standard"
    scaler_version: str = "moneybot-legacy-standard.v1"
    clip_lower: List[float] | None = None
    clip_upper: List[float] | None = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "feature_columns": self.feature_columns,
            "means": self.means,
            "stds": self.stds,
            "weights": self.weights,
            "bias": self.bias,
            "decision_threshold": self.decision_threshold,
            "calibration_slope": self.calibration_slope,
            "calibration_intercept": self.calibration_intercept,
            "lineage": self.lineage,
            "forecast_horizon": self.forecast_horizon,
            "scaler_type": self.scaler_type,
            "scaler_version": self.scaler_version,
            "clip_lower": self.clip_lower,
            "clip_upper": self.clip_upper,
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
    # News factors may be pre-populated by snapshot builders. Keep deterministic defaults if absent.
    out["news_sentiment_score"] = out.get("news_sentiment_score", 0.0)
    out["news_headline_count_24h"] = out.get("news_headline_count_24h", 0.0)
    out["news_source_score_72h"] = out.get("news_source_score_72h", 0.0)
    out["news_momentum_24h"] = out.get("news_momentum_24h", 0.0)
    out["news_momentum_72h"] = out.get("news_momentum_72h", 0.0)

    return out


def attach_labels(
    feature_df: pd.DataFrame, horizon_days: int = 5, target_return: float = 0.0
) -> pd.DataFrame:
    """Add forward-return and binary label columns for supervised training."""
    if "Close" not in feature_df.columns:
        raise ValueError("Close column is required to compute labels")
    out = feature_df.copy()
    forward_close = out["Close"].shift(-horizon_days)
    out[f"forward_return_{horizon_days}d"] = (forward_close / out["Close"]) - 1.0
    out[f"label_up_{horizon_days}d"] = (
        out[f"forward_return_{horizon_days}d"] > target_return
    ).astype(float)
    return out


def build_training_matrix(
    labeled_df: pd.DataFrame, horizon_days: int = 5
) -> tuple[np.ndarray, np.ndarray, pd.DataFrame]:
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


def _weighted_quantile(
    values: np.ndarray, quantile: float, weights: np.ndarray
) -> np.ndarray:
    """Return deterministic per-column weighted quantiles."""
    results = []
    for column in range(values.shape[1]):
        order = np.argsort(values[:, column], kind="stable")
        ordered_values = values[order, column]
        ordered_weights = weights[order]
        cumulative = np.cumsum(ordered_weights) - (0.5 * ordered_weights)
        cumulative /= ordered_weights.sum()
        results.append(float(np.interp(quantile, cumulative, ordered_values)))
    return np.asarray(results, dtype=float)


def train_logistic_baseline(
    X: np.ndarray,
    y: np.ndarray,
    *,
    learning_rate: float = 0.1,
    epochs: int = 400,
    l2: float = 1e-3,
    decision_threshold: float = 0.55,
    sample_weight: np.ndarray | None = None,
) -> BaselineModelArtifact:
    """Train deterministic logistic regression using full-batch gradient descent."""
    if X.ndim != 2:
        raise ValueError("X must be a 2D array")
    if y.ndim != 1:
        raise ValueError("y must be a 1D array")
    if X.shape[0] != y.shape[0]:
        raise ValueError("X and y must have matching rows")

    if sample_weight is None:
        row_weights = np.ones_like(y, dtype=float)
    else:
        row_weights = np.asarray(sample_weight, dtype=float)
        if row_weights.ndim != 1:
            raise ValueError("sample_weight must be a 1D array")
        if row_weights.shape[0] != y.shape[0]:
            raise ValueError("sample_weight and y must have matching rows")
        if not np.isfinite(row_weights).all():
            raise ValueError("sample_weight must contain finite values")
        if (row_weights < 0).any():
            raise ValueError("sample_weight cannot contain negative values")
        mean_weight = float(row_weights.mean())
        if mean_weight <= 0.0:
            raise ValueError("sample_weight must contain at least one positive value")
        row_weights = row_weights / mean_weight

    if not np.isfinite(X).all():
        raise ValueError("X must contain finite values")
    transformed = np.asarray(X, dtype=float)
    clip_lower = clip_upper = None
    if winsor_quantiles is not None:
        lower_q, upper_q = winsor_quantiles
        if not 0.0 <= lower_q < upper_q <= 1.0:
            raise ValueError("winsor_quantiles must satisfy 0 <= lower < upper <= 1")
        clip_lower = _weighted_quantile(transformed, lower_q, row_weights)
        clip_upper = _weighted_quantile(transformed, upper_q, row_weights)
        transformed = np.clip(transformed, clip_lower, clip_upper)

    if scaler_type == "legacy_standard":
        means = transformed.mean(axis=0)
        stds = transformed.std(axis=0)
        scaler_version = "moneybot-legacy-standard.v1"
    elif scaler_type == "weighted_standard":
        weight_sum = float(row_weights.sum())
        means = np.sum(transformed * row_weights[:, None], axis=0) / weight_sum
        variance = (
            np.sum(row_weights[:, None] * (transformed - means) ** 2, axis=0)
            / weight_sum
        )
        stds = np.sqrt(variance)
        scaler_version = "moneybot-weighted-standard.v1"
    elif scaler_type == "robust_iqr":
        means = _weighted_quantile(transformed, 0.5, row_weights)
        stds = _weighted_quantile(transformed, 0.75, row_weights) - _weighted_quantile(
            transformed, 0.25, row_weights
        )
        scaler_version = "moneybot-robust-iqr.v1"
    else:
        raise ValueError(f"Unsupported scaler_type: {scaler_type}")
    stds = np.where(np.isfinite(stds) & (np.abs(stds) > 1e-12), stds, 1.0)

    Xn = (transformed - means) / stds
    weights = np.zeros(Xn.shape[1], dtype=float)
    bias = 0.0

    if sample_weight is None:
        row_weights = np.ones_like(y, dtype=float)
    else:
        row_weights = np.asarray(sample_weight, dtype=float)
        if row_weights.ndim != 1:
            raise ValueError("sample_weight must be a 1D array")
        if row_weights.shape[0] != y.shape[0]:
            raise ValueError("sample_weight and y must have matching rows")
        if not np.isfinite(row_weights).all():
            raise ValueError("sample_weight must contain finite values")
        if (row_weights < 0).any():
            raise ValueError("sample_weight cannot contain negative values")
        mean_weight = float(row_weights.mean())
        if mean_weight <= 0.0:
            raise ValueError("sample_weight must contain at least one positive value")
        row_weights = row_weights / mean_weight

    n = float(Xn.shape[0])
    for _ in range(epochs):
        logits = (Xn @ weights) + bias
        preds = _sigmoid(logits)
        error = (preds - y) * row_weights

        grad_w = (Xn.T @ error) / n + (l2 * weights)
        grad_b = float(error.mean())

        weights -= learning_rate * grad_w
        bias -= learning_rate * grad_b

    return BaselineModelArtifact(
        version="alpha-atlas-v1",
        feature_columns=list(FEATURE_COLUMNS),
        means=means.tolist(),
        stds=stds.tolist(),
        weights=weights.tolist(),
        bias=float(bias),
        decision_threshold=float(decision_threshold),
        scaler_type=scaler_type,
        scaler_version=scaler_version,
        clip_lower=clip_lower.tolist() if clip_lower is not None else None,
        clip_upper=clip_upper.tolist() if clip_upper is not None else None,
    )


def default_baseline_artifact() -> BaselineModelArtifact:
    """Built-in fallback artifact used when external artifact file is unavailable."""
    return BaselineModelArtifact(
        version="alpha-atlas-v1-fallback",
        feature_columns=list(FEATURE_COLUMNS),
        means=[0.0, 0.0, 50.0, 0.0, 1.0, 0.0, 1.0, 0.7, 0.0, 0.0],
        stds=[1.0, 1.0, 10.0, 1.0, 1.0, 0.4, 2.0, 0.3, 0.5, 0.5],
        weights=[0.28, 0.2, -0.1, 0.44, 0.09, 0.12, 0.03, 0.08, 0.09, 0.11],
        bias=0.1,
        decision_threshold=0.55,
    )


def predict_proba(artifact: BaselineModelArtifact, rows: np.ndarray) -> np.ndarray:
    if rows.ndim == 1:
        rows = rows.reshape(1, -1)
    means = np.asarray(artifact.means, dtype=float)
    stds = np.asarray(artifact.stds, dtype=float)
    weights = np.asarray(artifact.weights, dtype=float)
    transformed = np.asarray(rows, dtype=float)
    if artifact.clip_lower is not None or artifact.clip_upper is not None:
        if artifact.clip_lower is None or artifact.clip_upper is None:
            raise ValueError("Artifact clipping requires both lower and upper bounds")
        transformed = np.clip(
            transformed,
            np.asarray(artifact.clip_lower, dtype=float),
            np.asarray(artifact.clip_upper, dtype=float),
        )
    rows_n = (transformed - means) / stds
    logits = rows_n @ weights + float(artifact.bias)
    logits = (logits * float(artifact.calibration_slope)) + float(
        artifact.calibration_intercept
    )
    return _sigmoid(logits)


def fit_probability_calibration(
    raw_probs: np.ndarray,
    labels: np.ndarray,
    *,
    learning_rate: float = 0.05,
    epochs: int = 500,
) -> dict[str, float | int | bool | str]:
    """Fit conservative Platt scaling, retaining identity if it worsens Brier."""
    probs = np.clip(np.asarray(raw_probs, dtype=float), 1e-6, 1.0 - 1e-6)
    y = np.asarray(labels, dtype=float)
    if probs.ndim != 1 or y.ndim != 1 or len(probs) != len(y):
        raise ValueError("raw_probs and labels must be matching 1D arrays")
    raw_brier = float(np.mean((probs - y) ** 2)) if len(y) else 0.0
    if len(y) < 2 or len(np.unique(y)) < 2:
        return {
            "method": "identity",
            "slope": 1.0,
            "intercept": 0.0,
            "rows": int(len(y)),
            "raw_brier_score": raw_brier,
            "calibrated_brier_score": raw_brier,
            "applied": False,
            "reason": "calibration period needs both classes",
        }

    logits = np.log(probs / (1.0 - probs))
    slope = 1.0
    intercept = 0.0
    for _ in range(epochs):
        calibrated = _sigmoid((slope * logits) + intercept)
        error = calibrated - y
        slope -= learning_rate * float(np.mean(error * logits))
        intercept -= learning_rate * float(np.mean(error))

    calibrated_probs = _sigmoid((slope * logits) + intercept)
    calibrated_brier = float(np.mean((calibrated_probs - y) ** 2))
    applied = calibrated_brier < raw_brier
    return {
        "method": "platt" if applied else "identity",
        "slope": float(slope) if applied else 1.0,
        "intercept": float(intercept) if applied else 0.0,
        "rows": int(len(y)),
        "raw_brier_score": raw_brier,
        "calibrated_brier_score": calibrated_brier if applied else raw_brier,
        "applied": applied,
        "reason": (
            "calibrated Brier improved"
            if applied
            else "identity retained because calibration did not improve Brier"
        ),
    }


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
        calibration_slope=float(payload.get("calibration_slope", 1.0)),
        calibration_intercept=float(payload.get("calibration_intercept", 0.0)),
        lineage=(
            payload.get("lineage") if isinstance(payload.get("lineage"), dict) else None
        ),
        forecast_horizon=(
            str(payload["forecast_horizon"])
            if payload.get("forecast_horizon") is not None
            else None
        ),
        scaler_type=str(payload.get("scaler_type") or "legacy_standard"),
        scaler_version=str(
            payload.get("scaler_version") or "moneybot-legacy-standard.v1"
        ),
        clip_lower=(
            [float(v) for v in payload["clip_lower"]]
            if isinstance(payload.get("clip_lower"), list)
            else None
        ),
        clip_upper=(
            [float(v) for v in payload["clip_upper"]]
            if isinstance(payload.get("clip_upper"), list)
            else None
        ),
    )


def chronological_split(
    df: pd.DataFrame, train_ratio: float = 0.8
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if not 0.1 <= train_ratio <= 0.95:
        raise ValueError("train_ratio must be between 0.1 and 0.95")
    pivot = int(len(df) * train_ratio)
    if pivot <= 0 or pivot >= len(df):
        raise ValueError("train_ratio creates an empty train or test split")
    return df.iloc[:pivot].copy(), df.iloc[pivot:].copy()


def summarize_binary_predictions(
    y_true: Iterable[float], y_pred: Iterable[int]
) -> Dict[str, float]:
    yt = np.asarray(list(y_true), dtype=int)
    yp = np.asarray(list(y_pred), dtype=int)
    if yt.shape != yp.shape:
        raise ValueError("y_true and y_pred must be same shape")
    accuracy = float((yt == yp).mean()) if len(yt) else 0.0
    positive_rate = float(yp.mean()) if len(yp) else 0.0
    return {
        "accuracy": round(accuracy, 4),
        "positive_rate": round(positive_rate, 4),
        "rows": float(len(yt)),
    }
