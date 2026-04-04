from scripts.autofill_daily_report import build_daily_report_markdown


def test_build_daily_report_markdown_includes_core_sections():
    md = build_daily_report_markdown(
        summary={"events_considered": 10, "source_counts": {"deterministic_model": 7}, "endpoint_counts": {"quick_ask": 8}},
        outcomes={"data": {"summary_1d": {"accuracy": 0.6, "evaluated_rows": 5}, "summary_5d": {"accuracy": 0.7, "evaluated_rows": 4}}},
        calibration={"rows": 12, "brier_score": 0.21, "recommended": {"intercept_delta": 0.03}},
        plan={"apply_change": True, "current": {"slope": 1.0}, "next": {"slope": 1.0}},
        recent_changes=["abc123 sample commit"],
    )

    assert "Moneybot Daily Ops Report" in md
    assert "Decision Activity" in md
    assert "Outcomes Snapshot" in md
    assert "Calibration" in md
    assert "Push vs Local" in md
    assert "abc123 sample commit" in md
