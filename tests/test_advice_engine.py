from advice_engine import compute_user_advice


def test_profitable_with_reversal_is_sell():
    out = compute_user_advice(
        symbol="ABC",
        entry_price=100,
        quote={"price": 120, "change_percent": -1.2},
        technical={"rsi": 74, "macd_histogram": -0.3, "trend": "bearish"},
        sentiment={"score": 0.3, "label": "negative", "headlines": ["weak demand"]},
        base_action="HOLD",
        hybrid_score=8.1,
    )
    assert out["advice"] == "SELL"


def test_profitable_no_reversal_is_hold():
    out = compute_user_advice(
        symbol="ABC",
        entry_price=100,
        quote={"price": 112, "change_percent": 0.5},
        technical={"rsi": 58, "macd_histogram": 0.2, "trend": "bullish"},
        sentiment={"score": 0.62, "label": "positive", "headlines": ["steady growth"]},
        base_action="BUY",
        hybrid_score=7.2,
    )
    assert out["advice"] == "HOLD"


def test_dip_oversold_improving_is_buy():
    out = compute_user_advice(
        symbol="ABC",
        entry_price=100,
        quote={"price": 94, "change_percent": -2.1},
        technical={"rsi": 30, "macd_histogram": 0.1, "trend": "bullish"},
        sentiment={"score": 0.61, "label": "positive", "headlines": ["rebound signs"]},
        base_action="HOLD",
        hybrid_score=6.5,
    )
    assert out["advice"] == "BUY"


def test_loss_but_oversold_is_hold():
    out = compute_user_advice(
        symbol="ABC",
        entry_price=100,
        quote={"price": 97, "change_percent": -0.8},
        technical={"rsi": 28, "macd_histogram": 0.05, "trend": "bullish"},
        sentiment={"score": 0.52, "label": "neutral", "headlines": []},
        base_action="SELL",
        hybrid_score=4.1,
    )
    assert out["advice"] == "HOLD"


def test_missing_data_is_hold_with_reason():
    out = compute_user_advice(
        symbol="ABC",
        entry_price=100,
        quote={"price": "DATA_MISSING", "change_percent": "DATA_MISSING"},
        technical={"rsi": None, "macd_histogram": None, "trend": "unknown"},
        sentiment={"score": None, "label": "neutral", "headlines": []},
        base_action="BUY",
        hybrid_score=None,
    )
    assert out["advice"] == "HOLD"
    assert "Data missing" in out["reason_summary"]


def test_sentiment_boost_adds_confidence_and_trigger_text():
    out = compute_user_advice(
        symbol="ABC",
        entry_price=100,
        quote={"price": 110, "change_percent": 1.1},
        technical={"rsi": 54, "macd_histogram": 0.2, "trend": "bullish"},
        sentiment={"score": 0.72, "label": "positive", "headlines": ["great outlook"]},
        base_action="BUY",
        hybrid_score=7.0,
    )
    assert out["confidence_score"] == 8.5
    assert "Sentiment boost: +1.5" in out["reason_summary"]


def test_negative_sentiment_reduces_confidence():
    out = compute_user_advice(
        symbol="ABC",
        entry_price=100,
        quote={"price": 95, "change_percent": -1.0},
        technical={"rsi": 49, "macd_histogram": -0.1, "trend": "bearish"},
        sentiment={"score": 0.32, "label": "negative", "headlines": ["concerns"]},
        base_action="HOLD",
        hybrid_score=6.0,
    )
    assert out["confidence_score"] == 5.0
