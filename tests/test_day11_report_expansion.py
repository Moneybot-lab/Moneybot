import pandas as pd

from moneybot.services.deterministic_model import BaselineModelArtifact, save_artifact
from scripts.day11_compare_candidate_vs_production import _prediction_error_examples, _promotion_decision


def _artifact(path, *, threshold):
    save_artifact(
        BaselineModelArtifact(
            version=path.stem,
            feature_columns=["feature_signal"],
            means=[0.0],
            stds=[1.0],
            weights=[10.0],
            bias=0.0,
            decision_threshold=threshold,
        ),
        path,
    )


def test_prediction_error_examples_include_threshold_overlap_and_symbol_date_rows(tmp_path):
    candidate_path = tmp_path / "candidate.json"
    production_path = tmp_path / "production.json"
    _artifact(candidate_path, threshold=0.55)
    _artifact(production_path, threshold=0.95)
    frame = pd.DataFrame(
        [
            {"symbol": "LOSS", "event_date": "2026-07-20", "feature_signal": 0.1, "return_5d": -0.05, "return_bin_5d": "big_loss"},
            {"symbol": "GAIN", "event_date": "2026-07-21", "feature_signal": -1.0, "return_5d": 0.05, "return_bin_5d": "big_gain"},
        ]
    )

    examples = _prediction_error_examples(str(candidate_path), str(production_path), frame)

    assert examples["chosen_threshold"] == 0.55
    assert examples["prediction_overlap"]["rows"] == 2
    assert examples["prediction_overlap"]["shared_positive_predictions"] == 0
    assert examples["big_loss_false_positive_count"] == 1
    assert examples["big_loss_false_positives"][0]["symbol"] == "LOSS"
    assert examples["big_loss_false_positives"][0]["event_date"] == "2026-07-20"
    assert examples["missed_big_gain_count"] == 1
    assert examples["missed_big_gain_rows"][0]["symbol"] == "GAIN"
    assert examples["missed_big_gain_rows"][0]["event_date"] == "2026-07-21"


def test_promotion_decision_labels_promote_hold_watch_and_no_op_clone():
    assert _promotion_decision(True, False, True, True, True) == "PROMOTE"
    assert _promotion_decision(False, True, True, True, True) == "NO_OP_CLONE"
    assert _promotion_decision(False, False, True, True, False) == "WATCH"
    assert _promotion_decision(False, False, False, True, True) == "HOLD"
