import json

from scripts.backtest_challenger_suite import backtest_challenger_suite
from scripts.train_challenger_suite import train_challenger_suite


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

    assert report["schema_version"] == "moneybot-challenger-backtest.v1"
    assert report["routing_policy"].startswith("shadow-log first")
    assert "buy_and_hold_return" in report["benchmark"]
    assert len(report["challengers"]) == suite["challenger_count"]
    first = report["challengers"][0]
    assert "total_return_net" in first["backtest_metrics"]
    assert "max_drawdown" in first["backtest_metrics"]
    assert "calibration" in first["backtest_metrics"]
    assert "drift" in first["backtest_metrics"]
    assert first["promotion_gates"]["objective_gates"]["min_rows"] == 10
    assert first["routing_allowed"] is False
