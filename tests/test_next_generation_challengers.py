import json
from pathlib import Path

import pandas as pd

from scripts.generate_next_generation_challengers import generate
from scripts.train_massive_baseline_model import train_massive_baseline


def _write(path: Path, rows: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _rows(start: str, dates: int, rows_per_date: int) -> list[dict]:
    rows = []
    for date_index, date in enumerate(pd.date_range(start, periods=dates, freq="D")):
        for row_index in range(rows_per_date):
            signal = ((date_index * 7 + row_index * 3) % 20) / 20.0
            regime = 1.0 if date_index % 3 else -1.0
            return_5d = (signal - 0.48) * 0.12 + (0.008 * regime)
            rows.append({
                "event_date": date.strftime("%Y-%m-%d"),
                "symbol": f"SYM{row_index % 12}",
                "endpoint": "watch",
                "decision_source": "market",
                "label_up_5d": int(return_5d > 0.0),
                "return_5d": return_5d,
                "feature_return_5d_lagged": (signal - 0.5) * 0.08,
                "feature_rsi_14": 35.0 + (signal * 30.0),
                "feature_relative_volume_20d": 0.7 + signal,
                "feature_spy_return_5d": 0.01 * regime,
                "feature_market_regime_risk_on": regime,
            })
    return rows


def test_generates_five_distinct_research_only_families(tmp_path):
    quality = tmp_path / "training_quality"
    train_path = quality / "cleaned_train.jsonl"
    test_path = quality / "cleaned_test.jsonl"
    all_path = quality / "cleaned_all.jsonl"
    train_rows = _rows("2026-01-01", 60, 12)
    test_rows = _rows("2026-03-10", 12, 20)
    _write(train_path, train_rows)
    _write(test_path, test_rows)
    _write(all_path, train_rows + test_rows)
    baseline_path = tmp_path / "massive_baseline_model_v1.json"
    train_massive_baseline(train_path, test_path, all_path, baseline_path)
    comparison_path = tmp_path / "comparison.json"
    comparison_path.write_text(json.dumps({
        "comparison_valid": True,
        "comparison_scope_report": {"apples_to_apples_scoring": True},
    }), encoding="utf-8")
    output_dir = tmp_path / "next_generation"

    manifest = generate(train_path, test_path, all_path, baseline_path, comparison_path, output_dir)

    expected = {
        "candidate_risk_adjusted_return_v1",
        "candidate_big_loss_filter_v1",
        "candidate_big_gain_ranker_v1",
        "candidate_threshold_sweep_v1",
        "candidate_regime_split_v1",
    }
    assert {item["model_version"] for item in manifest["challengers"]} == expected
    assert len({item["recipe_hash"] for item in manifest["challengers"]}) == 5
    assert all(not item["clone_detection"]["no_op_clone"] for item in manifest["challengers"])
    assert all(item["promotion_ready"] is False for item in manifest["challengers"])
    assert all(item["routing_allowed"] is False for item in manifest["challengers"])
    assert all(item["same_cleaned_holdout_rows"] is True for item in manifest["challengers"])
    for model_version in expected:
        assert (output_dir / f"{model_version}.json").exists()
    for filename in (
        "next_generation_challenger_manifest.json",
        "challenger_vs_massive_baseline_report.json",
        "risk_adjusted_challenger_report.json",
        "big_loss_filter_report.json",
        "big_gain_ranker_report.json",
        "threshold_overlay_report.json",
        "regime_split_report.json",
    ):
        assert (output_dir / filename).exists()


def test_generation_refuses_invalid_comparison(tmp_path):
    comparison_path = tmp_path / "comparison.json"
    comparison_path.write_text(json.dumps({"comparison_valid": False}), encoding="utf-8")
    try:
        generate(tmp_path / "train", tmp_path / "test", tmp_path / "all", tmp_path / "baseline", comparison_path, tmp_path / "output")
    except ValueError as exc:
        assert "valid apples-to-apples" in str(exc)
    else:
        raise AssertionError("invalid comparison should block next-generation generation")
