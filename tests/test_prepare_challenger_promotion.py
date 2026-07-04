import json

from scripts.prepare_challenger_promotion import prepare_challenger_promotion


def test_prepare_challenger_promotion_selects_gate_cleared_logistic_model(tmp_path):
    model = tmp_path / "models" / "good.json"
    model.parent.mkdir()
    model.write_text(json.dumps({"version": "good", "model_type": "logistic_regression"}), encoding="utf-8")
    report = {
        "ranked_model_versions": ["stump", "good"],
        "benchmark": {"buy_and_hold_return": 0.01},
        "challengers": [
            {"model_version": "stump", "model_type": "decision_stump", "model_path": str(model), "promotion_gates": {"promotion_ready": True}, "routing_allowed": False},
            {"model_version": "good", "model_type": "logistic_regression", "model_path": str(model), "backtest_metrics": {"total_return_net": 0.02}, "promotion_gates": {"promotion_ready": True}, "routing_allowed": False},
        ],
    }
    report_path = tmp_path / "backtest.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    result = prepare_challenger_promotion(backtest_report_path=report_path, output_dir=tmp_path / "out")

    comparison = json.loads((tmp_path / "out" / "model_comparison_track_b.json").read_text(encoding="utf-8"))
    candidate = json.loads((tmp_path / "out" / "candidate_model_track_b.json").read_text(encoding="utf-8"))
    assert result["candidate_win"] is True
    assert comparison["selected_model_version"] == "good"
    assert candidate["version"] == "good"


def test_prepare_challenger_promotion_writes_losing_report_when_no_model_clears_gates(tmp_path):
    report_path = tmp_path / "backtest.json"
    report_path.write_text(json.dumps({"ranked_model_versions": [], "challengers": []}), encoding="utf-8")

    result = prepare_challenger_promotion(backtest_report_path=report_path, output_dir=tmp_path / "out")

    assert result["candidate_win"] is False
    assert (tmp_path / "out" / "model_comparison_track_b.json").exists()
    assert (tmp_path / "out" / "candidate_model_track_b.json").exists()
