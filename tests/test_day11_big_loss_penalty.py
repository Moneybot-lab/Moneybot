import json
from pathlib import Path

from scripts.day11_compare_candidate_vs_production import HARD_BIG_LOSS_FALSE_POSITIVE_PENALTY, _decide


def _metrics(**overrides):
    base = {
        "rows": 250,
        "accuracy": 0.7,
        "brier_score": 0.1,
        "avg_return": 0.1,
        "downside_risk": 0.0,
        "big_loss_predictions": 0,
        "big_loss_prediction_rate": 0.0,
        "big_gain_capture_rate": 0.2,
    }
    base.update(overrides)
    return base


def test_decide_blocks_candidate_big_loss_false_positive_when_production_has_zero():
    candidate = _metrics(accuracy=0.8, brier_score=0.05, avg_return=1.2, big_loss_predictions=1, big_loss_prediction_rate=0.1)
    production = _metrics(accuracy=0.7, brier_score=0.1, avg_return=0.1, big_loss_predictions=0, big_loss_prediction_rate=0.0)

    candidate_win, reasons = _decide(candidate, production, min_rows=200)

    assert candidate_win is False
    assert candidate["big_loss_false_positive_penalty"] == HARD_BIG_LOSS_FALSE_POSITIVE_PENALTY
    assert candidate["utility_score_after_big_loss_penalty"] < candidate["avg_return"]
    assert "candidate predicts big-loss rows while production predicts zero; hard false-positive penalty applied" in reasons
    assert "candidate big_loss_prediction_rate exceeds production" in reasons


def test_decide_keeps_zero_big_loss_candidate_eligible():
    candidate = _metrics(accuracy=0.8, brier_score=0.05, avg_return=0.2, big_loss_predictions=0, big_loss_prediction_rate=0.0)
    production = _metrics(accuracy=0.7, brier_score=0.1, avg_return=0.1, big_loss_predictions=0, big_loss_prediction_rate=0.0)

    candidate_win, reasons = _decide(candidate, production, min_rows=200)

    assert candidate_win is True
    assert candidate["big_loss_false_positive_penalty"] == 0.0
    assert "candidate improves profit utility with acceptable brier, return/downside, big-loss avoidance, and minimum big-gain capture" in reasons


def test_stored_cmi_regression_example_remains_a_hard_promotion_block():
    example = json.loads(Path("regression_examples/track_b_cmi_2026-07-10.json").read_text(encoding="utf-8"))
    candidate = _metrics(brier_score=0.01, avg_return=2.0, big_loss_predictions=1, big_loss_prediction_rate=0.01)
    production = _metrics(big_loss_predictions=0, big_loss_prediction_rate=0.0)

    candidate_win, reasons = _decide(candidate, production, min_rows=200)

    assert example["symbol"] == "CMI"
    assert example["event_date"] == "2026-07-10"
    assert example["observed_comparison"]["candidate_prediction"] == 1
    assert example["observed_comparison"]["production_prediction"] == 0
    assert example["expected_guardrails"]["must_not_support_promotion"] is True
    assert candidate_win is False
    assert candidate["big_loss_false_positive_penalty"] == HARD_BIG_LOSS_FALSE_POSITIVE_PENALTY
    assert any("hard false-positive penalty applied" in reason for reason in reasons)


def test_decide_blocks_symbol_or_date_concentrated_candidate_utility():
    candidate = _metrics(
        accuracy=0.8,
        brier_score=0.05,
        avg_return=0.2,
        symbol_utility_concentration=0.75,
        date_utility_concentration=0.80,
    )
    production = _metrics()

    candidate_win, reasons = _decide(candidate, production, min_rows=200)

    assert candidate_win is False
    assert any("too concentrated in one symbol" in reason for reason in reasons)
    assert any("too concentrated on one date" in reason for reason in reasons)


def test_decide_blocks_candidate_requiring_feature_risk_review():
    candidate = _metrics(accuracy=0.8, brier_score=0.05, avg_return=0.2, feature_risk_audit={"requires_review": True})
    production = _metrics()

    candidate_win, reasons = _decide(candidate, production, min_rows=200)

    assert candidate_win is False
    assert "candidate feature-risk audit requires review" in reasons
