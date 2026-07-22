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
