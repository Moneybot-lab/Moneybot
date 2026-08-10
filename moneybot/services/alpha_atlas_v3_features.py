from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any, Iterable

FEATURE_CONTRACT_VERSION = "moneybot-serving-features.v1"
FEATURE_ENGINE_VERSION = "alpha-atlas-v3-market-state.v1"
FORECAST_HORIZON = "5d"

ALPHA_ATLAS_V3_FEATURES = (
    "feature_return_1d_lagged",
    "feature_return_5d_lagged",
    "feature_return_20d_lagged",
    "feature_rsi_14",
    "feature_macd_hist",
    "feature_volume_ratio_20d",
    "feature_price_vs_sma_20",
    "feature_volatility_20d",
    "feature_spy_return_5d",
    "feature_symbol_minus_spy_5d",
    "feature_market_regime_risk_on",
)

FORBIDDEN_V3_FEATURE_FRAGMENTS = (
    "probability_up",
    "feature_rec_",
    "feature_source_",
    "future_",
    "forward_",
    "realized_return",
    "outcome_",
    "label_",
)


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _bar_date(bar: dict[str, Any]) -> str:
    value = (
        bar.get("start_timestamp")
        or bar.get("date")
        or bar.get("timestamp_utc")
        or bar.get("t")
        or ""
    )
    if isinstance(value, (int, float)):
        divisor = 1000 if float(value) > 100_000_000_000 else 1
        return datetime.utcfromtimestamp(float(value) / divisor).date().isoformat()
    return str(value)[:10]


def normalize_daily_bars(
    bars: Iterable[dict[str, Any]], *, asof_date: date | datetime | str | None = None
) -> list[dict[str, Any]]:
    cutoff = str(
        asof_date.date() if isinstance(asof_date, datetime) else asof_date or ""
    )[:10]
    normalized: dict[str, dict[str, Any]] = {}
    for raw in bars:
        if not isinstance(raw, dict):
            continue
        day = _bar_date(raw)
        close = _number(
            raw.get("close") if raw.get("close") is not None else raw.get("c")
        )
        if not day or close is None or (cutoff and day > cutoff):
            continue
        volume = _number(
            raw.get("volume") if raw.get("volume") is not None else raw.get("v")
        )
        normalized[day] = {"date": day, "close": close, "volume": volume}
    return [normalized[day] for day in sorted(normalized)]


def _return(closes: list[float], bars: int) -> float | None:
    if len(closes) <= bars or closes[-bars - 1] == 0:
        return None
    return round((closes[-1] / closes[-bars - 1]) - 1.0, 6)


def _sma(values: list[float], window: int) -> float | None:
    if len(values) < window:
        return None
    return sum(values[-window:]) / window


def _ema_series(values: list[float], span: int) -> list[float]:
    if not values:
        return []
    alpha = 2.0 / (span + 1.0)
    result = [float(values[0])]
    for value in values[1:]:
        result.append((float(value) * alpha) + (result[-1] * (1.0 - alpha)))
    return result


def _rsi(values: list[float], window: int = 14) -> float | None:
    if len(values) <= window:
        return None
    changes = [
        values[pos] - values[pos - 1]
        for pos in range(len(values) - window, len(values))
    ]
    avg_gain = sum(max(change, 0.0) for change in changes) / window
    avg_loss = sum(max(-change, 0.0) for change in changes) / window
    if avg_loss == 0:
        return 100.0
    return round(100.0 - (100.0 / (1.0 + (avg_gain / avg_loss))), 6)


def _macd_hist(values: list[float]) -> float | None:
    if len(values) < 34:
        return None
    ema12 = _ema_series(values, 12)
    ema26 = _ema_series(values, 26)
    macd = [ema12[pos] - ema26[pos] for pos in range(25, len(values))]
    signal = _ema_series(macd, 9)
    return round(macd[-1] - signal[-1], 6)


def _volatility(values: list[float], window: int = 20) -> float | None:
    if len(values) <= window:
        return None
    returns = [
        (values[pos] / values[pos - 1]) - 1.0
        for pos in range(len(values) - window, len(values))
        if values[pos - 1] != 0
    ]
    if len(returns) != window:
        return None
    mean = sum(returns) / window
    return round((sum((value - mean) ** 2 for value in returns) / window) ** 0.5, 6)


def build_alpha_atlas_v3_features(
    *,
    symbol_bars: Iterable[dict[str, Any]],
    spy_bars: Iterable[dict[str, Any]],
    asof_date: date | datetime | str | None = None,
) -> dict[str, float | None]:
    symbol = normalize_daily_bars(symbol_bars, asof_date=asof_date)
    spy = normalize_daily_bars(spy_bars, asof_date=asof_date)
    closes = [float(bar["close"]) for bar in symbol]
    volumes = [bar["volume"] for bar in symbol]
    spy_closes = [float(bar["close"]) for bar in spy]
    ret5 = _return(closes, 5)
    spy_ret5 = _return(spy_closes, 5)
    sma20 = _sma(closes, 20)
    volume20 = (
        _sma([float(value) for value in volumes], 20)
        if all(value is not None for value in volumes[-20:])
        else None
    )
    volume_ratio = (
        None
        if not volumes or volumes[-1] is None or volume20 in {None, 0}
        else round(float(volumes[-1]) / float(volume20), 6)
    )
    spy_sma20 = _sma(spy_closes, 20)
    return {
        "feature_return_1d_lagged": _return(closes, 1),
        "feature_return_5d_lagged": ret5,
        "feature_return_20d_lagged": _return(closes, 20),
        "feature_rsi_14": _rsi(closes, 14),
        "feature_macd_hist": _macd_hist(closes),
        "feature_volume_ratio_20d": volume_ratio,
        "feature_price_vs_sma_20": (
            None
            if not closes or sma20 in {None, 0}
            else round((closes[-1] / float(sma20)) - 1.0, 6)
        ),
        "feature_volatility_20d": _volatility(closes, 20),
        "feature_spy_return_5d": spy_ret5,
        "feature_symbol_minus_spy_5d": (
            None if ret5 is None or spy_ret5 is None else round(ret5 - spy_ret5, 6)
        ),
        "feature_market_regime_risk_on": (
            None
            if not spy_closes or spy_ret5 is None or spy_sma20 is None
            else float(spy_ret5 > 0 and spy_closes[-1] >= spy_sma20)
        ),
    }


def ordered_feature_row(
    features: dict[str, float | None], columns: Iterable[str]
) -> list[float | None]:
    return [features.get(str(column)) for column in columns]


def validate_v3_feature_columns(columns: Iterable[str]) -> list[str]:
    values = [str(column) for column in columns]
    return [
        column
        for column in values
        if column not in ALPHA_ATLAS_V3_FEATURES
        or any(
            fragment in column.lower() for fragment in FORBIDDEN_V3_FEATURE_FRAGMENTS
        )
    ]


def v3_feature_declarations() -> list[dict[str, Any]]:
    transformations = {
        "feature_return_1d_lagged": "close(T)/close(T-1 trading bar)-1 decimal",
        "feature_return_5d_lagged": "close(T)/close(T-5 trading bars)-1 decimal",
        "feature_return_20d_lagged": "close(T)/close(T-20 trading bars)-1 decimal",
        "feature_rsi_14": "simple-average RSI over T-13..T changes, 0-100",
        "feature_macd_hist": "EMA12(close)-EMA26(close) minus EMA9(MACD), through T",
        "feature_volume_ratio_20d": "volume(T)/mean(volume T-19..T)",
        "feature_price_vs_sma_20": "close(T)/mean(close T-19..T)-1 decimal",
        "feature_volatility_20d": "population stddev of 20 backward 1-bar decimal returns",
        "feature_spy_return_5d": "SPY close(T)/SPY close(T-5 trading bars)-1 decimal",
        "feature_symbol_minus_spy_5d": "symbol backward 5-bar return minus SPY backward 5-bar return",
        "feature_market_regime_risk_on": "1 iff SPY backward 5-bar return >0 and close(T)>=SMA20(T)",
    }
    return [
        {
            "feature_name": name,
            "source": "adjusted Massive daily bars at timestamp T or earlier",
            "available_at_prediction_time": True,
            "training_builder": FEATURE_ENGINE_VERSION,
            "serving_builder": FEATURE_ENGINE_VERSION,
            "transformation": transformations[name],
            "fill_policy": "fit_period_median",
            "time_semantics": "at_or_before_T",
            "required": True,
        }
        for name in ALPHA_ATLAS_V3_FEATURES
    ]
