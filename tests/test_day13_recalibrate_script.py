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


def test_build_recalibration_plan_applies_bounded_slope_delta():
    report = {"rows": 120, "recommended": {"intercept_delta": 0.0, "slope_delta": -0.8}}
    plan = build_recalibration_plan(
        report,
        current_slope=0.9,
        current_intercept=-0.45,
        max_slope_step=0.25,
        min_rows=30,
    )

    assert plan["apply_change"] is True
    assert plan["recommended_delta"]["bounded_slope_delta"] == -0.25
    assert plan["next"]["slope"] == 0.65


def test_build_recalibration_plan_includes_effective_brier_and_requires_projection():
    report = {
        "rows": 120,
        "brier_score_raw": 0.286486,
        "calibrated_brier_score": 0.245,
        "effective_brier_score": 0.245,
        "recommended": {"intercept_delta": 0.666503, "slope_delta": -0.353011},
    }

    plan = build_recalibration_plan(report, current_slope=1.0, current_intercept=0.0, min_rows=30)

    assert plan["apply_change"] is True
    assert plan["brier_score_raw"] == 0.286486
    assert plan["effective_brier_score"] == 0.245
    assert plan["next"]["intercept"] == 0.666503
    assert plan["next"]["slope"] == 0.646989
