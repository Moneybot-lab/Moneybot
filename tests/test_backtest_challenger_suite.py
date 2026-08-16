import json

import numpy as np
import pandas as pd

from scripts.backtest_challenger_suite import _bootstrap_confidence_bounds, _pareto_frontier, backtest_challenger_suite
from scripts.train_challenger_suite import train_challenger_suite


def test_date_block_bootstrap_is_deterministic_and_conservative():
    frame = pd.DataFrame({"event_date": ["2026-01-01"] * 2 + ["2026-01-02"] * 2 + ["2026-01-03"] * 2})
    returns = np.asarray([0.02, 0.01, -0.03, -0.02, 0.01, 0.01])

    first = _bootstrap_confidence_bounds(returns, frame, resamples=300)
    second = _bootstrap_confidence_bounds(returns, frame, resamples=300)

    assert first == second
    assert first["independent_date_blocks"] == 1
    assert first["method"] == "non_overlapping_horizon_date_block_bootstrap"
    assert first["avg_return_lower"] <= first["avg_return_median"] <= first["avg_return_upper"]
    assert first["avg_return_lower"] <= 0.0


def test_pareto_frontier_retains_tradeoffs_and_drops_dominated_candidate():
    def candidate(version, avg_return, brier, bootstrap_lower, drawdown, big_loss_rate):
        return {
            "model_version": version,
            "model_type": "logistic_regression",
            "candidate_lane": "decision",
            "backtest_metrics": {
                "avg_return_net": avg_return,
                "max_drawdown": drawdown,
                "big_loss_prediction_rate": big_loss_rate,
                "calibration": {"brier_score": brier},
                "bootstrap_confidence": {"avg_return_lower": bootstrap_lower},
            },
        }

    frontier = _pareto_frontier(
        [
            candidate("profit", 0.10, 0.20, 0.01, -0.10, 0.0),
            candidate("calibrated", 0.08, 0.10, 0.02, -0.05, 0.0),
            candidate("dominated", 0.05, 0.30, -0.01, -0.20, 0.20),
        ],
        "decision",
    )

    assert [item["model_version"] for item in frontier] == ["calibrated", "profit"]


def test_backtest_challenger_suite_scores_every_model_with_gates_and_benchmarks(tmp_path):
    input_path = tmp_path / "all.jsonl"
    rows = []
    for idx in range(80):
        up = int(idx % 4 in {1, 2})
        rows.append({
            "ts": idx,
            "symbol": "AAPL",
            "recommendation": "BUY" if up else "HOLD",
            "feature_close": 100 + idx,
            "feature_return_1d_lagged": (idx % 5) / 100,
            "feature_volume": 1000 + (idx * 3),
            "return_5d": 0.02 if up else -0.01,
            "label_up_5d": up,
        })
    input_path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    suite = train_challenger_suite(input_path, tmp_path / "models", min_rows=20)

    report = backtest_challenger_suite(
        suite_manifest_path=tmp_path / "models" / "challenger_suite_manifest.json",
        feature_store_path=input_path,
        output_path=tmp_path / "backtest.json",
        min_rows=10,
    )

    assert report["schema_version"] == "moneybot-challenger-backtest.v2"
    assert report["routing_policy"].startswith("shadow-log first")
    assert "within each event date" in report["ranking_policy"]
    assert report["benchmark"]["date_cohort_benchmark_avg_return"] is not None
    assert report["benchmark"]["deprecated_fields"]["buy_and_hold_return"]["value"] is None
    assert len(report["challengers"]) == suite["challenger_count"]
    first = report["challengers"][0]
    assert "total_return_net" in first["backtest_metrics"]
    assert "max_drawdown" in first["backtest_metrics"]
    assert "calibration" in first["backtest_metrics"]
    assert "top_k_ranking" in first["backtest_metrics"]
    assert "date_local_top_k_avg_return" in first["backtest_metrics"]["top_k_ranking"]
    assert "drift" in first["backtest_metrics"]
    assert first["backtest_metrics"]["bootstrap_confidence"]["method"] == "non_overlapping_horizon_date_block_bootstrap"
    assert first["backtest_metrics"]["bootstrap_confidence"]["avg_return_lower"] is not None
    assert first["promotion_gates"]["objective_gates"]["min_rows"] == 10
    assert first["promotion_gates"]["objective_gates"]["min_bootstrap_avg_return_lower"] == 0.0
    assert first["routing_allowed"] is False
    retained = set(report["retained_model_versions"])
    frontier = set(report["pareto_frontiers"]["decision"]["model_versions"]) | set(report["pareto_frontiers"]["ranking"]["model_versions"])
    assert retained == frontier
    assert report["pareto_frontiers"]["ranking"]["can_replace_main_decision_model"] is False
    assert "one overall winner" in report["retention_policy"]


def test_backtest_challenger_suite_rehydrates_derived_app_signal_features(tmp_path):
    input_path = tmp_path / "all.jsonl"
    rows = []
    for idx in range(40):
        up = int(idx % 2 == 0)
        rows.append({
            "ts": idx,
            "symbol": "AAPL",
            "endpoint": "quick_ask",
            "decision_source": "deterministic_model",
            "recommendation": "BUY" if up else "HOLD",
            "probability_up": 0.7 if up else 0.3,
            "feature_close": 100 + idx,
            "return_5d": 0.02 if up else -0.01,
            "label_up_5d": up,
        })
    input_path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    suite = train_challenger_suite(input_path, tmp_path / "models", min_rows=20)
    first_logreg = next(item for item in suite["challengers"] if item["model_type"] == "logistic_regression")
    artifact_path = tmp_path / "models" / f"{first_logreg['model_version']}.json"
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    artifact["feature_columns"] = artifact["feature_columns"] + ["feature_rec_buy", "feature_endpoint_quick_ask"]
    artifact["means"] = artifact["means"] + [0.0, 0.0]
    artifact["stds"] = artifact["stds"] + [1.0, 1.0]
    artifact["weights"] = artifact["weights"] + [0.0, 0.0]
    artifact_path.write_text(json.dumps(artifact), encoding="utf-8")

    report = backtest_challenger_suite(
        suite_manifest_path=tmp_path / "models" / "challenger_suite_manifest.json",
        feature_store_path=input_path,
        output_path=tmp_path / "backtest.json",
        min_rows=10,
    )

    assert len(report["challengers"]) == suite["challenger_count"]
    assert report["challengers"][0]["backtest_metrics"]["rows"] == 40


def test_candidate_usage_scope_and_gate_fields_keep_ranking_out_of_promotion():
    from scripts.backtest_challenger_suite import _candidate_gate_fields

    challenger = {"model_type": "ranking_lane_linear", "candidate_lane": "ranking"}
    metrics = {
        "rows": 100,
        "positive_rate": 0.10,
        "big_loss_prediction_rate": 0.0,
        "calibration": {"negative_calibration_slope": False},
    }
    gates = {"failed_gates": []}
    support = {"selected_positive_predictions": 20}

    fields = _candidate_gate_fields(challenger, metrics, gates, support)

    assert fields["usage_scope"] == "ranking_only_candidate"
    assert fields["promotion_ready"] is False
    assert fields["routing_allowed"] is False
    assert "not_main_decision_candidate" in fields["promotion_blocking_issues"]


def _write_constant_suite(tmp_path, rows):
    tmp_path.mkdir(parents=True, exist_ok=True)
    input_path = tmp_path / "panel.jsonl"
    input_path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    models = tmp_path / "models"
    models.mkdir()
    artifact = models / "challenger-baseline-always-up-v1.json"
    artifact.write_text(json.dumps({"version": "challenger-baseline-always-up-v1", "model_type": "baseline_classifier"}), encoding="utf-8")
    suite_path = models / "challenger_suite_manifest.json"
    suite_path.write_text(json.dumps({
        "feature_columns": ["feature_close"],
        "feature_fill_values": {"feature_close": 100.0},
        "challengers": [{
            "model_version": "challenger-baseline-always-up-v1",
            "model_type": "baseline_classifier",
            "model_path": str(artifact),
        }],
    }), encoding="utf-8")
    report = backtest_challenger_suite(
        suite_manifest_path=suite_path,
        feature_store_path=input_path,
        output_path=tmp_path / "backtest.json",
        min_rows=1,
    )
    always_up = next(item for item in report["challengers"] if "always-up" in item["model_version"])
    return report, always_up


def _panel_rows():
    return [
        {"event_date": date, "ts": index, "symbol": symbol, "feature_close": 100.0, "return_5d": ret, "label_up_5d": int(ret > 0)}
        for index, (date, symbol, ret) in enumerate([
            ("2026-01-01", "AAA", 0.10), ("2026-01-01", "BBB", 0.10),
            ("2026-01-02", "AAA", 0.10), ("2026-01-02", "BBB", 0.10),
        ])
    ]


def test_cross_sectional_economics_do_not_pseudo_compound_and_fail_closed(tmp_path):
    report, model = _write_constant_suite(tmp_path, _panel_rows())
    metrics = model["backtest_metrics"]
    assert report["benchmark"]["date_cohort_benchmark_avg_return"] == 0.10
    assert metrics["avg_selected_return_gross"] == 0.10
    assert metrics["avg_selected_return_net"] == 0.098
    assert metrics["total_return_net"] is None
    assert metrics["max_drawdown"] is None
    assert metrics["max_drawdown_evaluable"] is False
    assert "drawdown_evidence_unavailable" in model["promotion_gates"]["failed_gates"]
    assert model["promotion_ready"] is False


def test_economic_metrics_are_duplicate_and_order_invariant(tmp_path):
    rows = _panel_rows()
    base, base_model = _write_constant_suite(tmp_path / "base", rows)
    changed_rows = list(reversed(rows)) + [dict(rows[0]) for _ in range(100)]
    changed, changed_model = _write_constant_suite(tmp_path / "changed", changed_rows)
    benchmark_fields = ["date_cohort_benchmark_avg_return", "equal_weight_universe_avg_5d_return"]
    metric_fields = ["avg_selected_return_gross", "avg_selected_return_net", "date_cohort_avg_return_net", "excess_avg_return_vs_universe"]
    assert {key: base["benchmark"][key] for key in benchmark_fields} == {key: changed["benchmark"][key] for key in benchmark_fields}
    assert {key: base_model["backtest_metrics"][key] for key in metric_fields} == {key: changed_model["backtest_metrics"][key] for key in metric_fields}
    assert changed["duplicate_weighting_report"]["duplicate_row_count"] == 100


def test_date_local_ranking_is_primary_and_costs_are_position_based(tmp_path):
    report, model = _write_constant_suite(tmp_path, _panel_rows())
    metrics = model["backtest_metrics"]
    assert metrics["top_k_ranking"]["date_local_top_k"] == 5
    assert metrics["top_k_ranking"]["independent_ranking_dates"] == 2
    assert metrics["estimated_entries"] == 4
    assert metrics["estimated_exits"] == 4
    assert metrics["turnover"] is None
    assert "no adjacent-row inference" in metrics["turnover_method"]
    assert report["economic_backtest_validity"]["overlapping_horizon_handled"] is True
