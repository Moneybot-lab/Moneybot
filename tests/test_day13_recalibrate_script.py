from scripts.day13_recalibrate import build_recalibration_plan


def test_build_recalibration_plan_applies_bounded_intercept_delta():
    report = {
        "rows": 120,
        "recommended": {"intercept_delta": 0.6, "slope_delta": 0.0},
    }
    plan = build_recalibration_plan(
        report,
        current_slope=1.0,
        current_intercept=0.0,
        max_intercept_step=0.2,
        min_rows=30,
    )

    assert plan["apply_change"] is True
    assert plan["recommended_delta"]["bounded_intercept_delta"] == 0.2
    assert plan["next"]["intercept"] == 0.2


def test_build_recalibration_plan_skips_when_rows_below_threshold():
    report = {"rows": 5, "recommended": {"intercept_delta": -0.1, "slope_delta": 0.0}}
    plan = build_recalibration_plan(report, current_slope=1.0, current_intercept=0.1, min_rows=10)

    assert plan["apply_change"] is False
    assert plan["next"]["intercept"] == 0.1
