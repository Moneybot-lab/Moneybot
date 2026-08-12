from moneybot.services.historical_validation import (
    build_dataset_manifest,
    build_historical_validation_report,
    evaluate_promotion_gates,
    summarize_validation_rows,
)


def _rows(count=40):
    rows = []
    for index in range(count):
        action = "BUY" if index % 2 == 0 else "SELL"
        realized = 0.03 if action == "BUY" else -0.02
        rows.append({
            "symbol": f"SYM{index}",
            "action": action,
            "probability_up": 0.8 if action == "BUY" else 0.2,
            "return_5d": realized,
            "transaction_cost_bps": 10,
            "max_adverse_excursion": -0.01,
            "source_mode": "websocket",
            "is_stale": False,
            "personalization": {"base_action": action, "action": action, "profile_bucket": "moderate"},
        })
    return rows


def test_dataset_manifest_checksum_is_reproducible_and_auditable():
    first = build_dataset_manifest(dataset_id="test-v1", source="fixture", rows=_rows(4), includes_delisted=True)
    second = build_dataset_manifest(dataset_id="test-v1", source="fixture", rows=_rows(4), includes_delisted=True)

    assert first.rows == 4
    assert first.checksum == second.checksum
    assert len(first.checksum) == 64
    assert first.point_in_time is True
    assert first.adjustment_method == "split_dividend_adjusted"
    assert first.includes_delisted is True


def test_validation_metrics_include_calibration_friction_churn_and_data_quality():
    rows = _rows()
    rows[1]["source_mode"] = "rest_fallback"
    rows[1]["is_stale"] = True
    rows[2]["personalization"] = {"base_action": "BUY", "action": "HOLD", "profile_bucket": "conservative"}

    metrics = summarize_validation_rows(rows)

    assert metrics["evaluated_rows"] == 40
    assert metrics["brier_score"] == 0.04
    assert metrics["avg_net_return"] > 0
    assert metrics["buy_precision"] == 1.0
    assert metrics["sell_precision"] == 1.0
    assert metrics["fallback_rate"] == 0.025
    assert metrics["stale_data_rate"] == 0.025
    assert metrics["profile_override_rate"] == 0.025
    assert metrics["by_profile"] == {"moderate": 39, "conservative": 1}


def test_promotion_gates_block_low_sample_unsafe_or_unreviewed_candidates():
    metrics = summarize_validation_rows(_rows(10))
    gates = evaluate_promotion_gates(metrics, min_rows=30)

    assert gates["promotion_ready"] is False
    failed = {gate["name"] for gate in gates["gates"] if not gate["passed"]}
    assert {"minimum_evaluated_rows", "licensing_review", "privacy_review"}.issubset(failed)


def test_historical_report_promotes_only_when_all_blocking_gates_pass():
    rows = _rows()
    manifest = build_dataset_manifest(dataset_id="candidate-v2", source="massive_flat_files", rows=rows, includes_delisted=True)
    report = build_historical_validation_report(
        rows=rows,
        dataset_manifest=manifest,
        baseline_metrics={"brier_score": 0.05, "worst_max_adverse_excursion": -0.02},
        gate_options={"licensing_review_complete": True, "privacy_review_complete": True},
        generated_at_utc="2026-06-08T00:00:00+00:00",
    )

    assert report["schema_version"] == "historical_validation.v1"
    assert report["promotion_gates"]["promotion_ready"] is True
    assert report["rollout_recommendation"] == "promote"
    assert report["required_next_steps"] == []
    assert report["dataset_manifest"]["dataset_id"] == "candidate-v2"
