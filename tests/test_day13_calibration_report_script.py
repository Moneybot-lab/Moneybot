from scripts.day13_calibration_report import calibration_summary


def test_calibration_summary_computes_brier_and_bins():
    rows = [
        {"predicted": 0.8, "observed": 1.0},
        {"predicted": 0.7, "observed": 1.0},
        {"predicted": 0.3, "observed": 0.0},
        {"predicted": 0.2, "observed": 0.0},
    ]

    summary = calibration_summary(rows, bins=4)

    assert summary["rows"] == 4
    assert isinstance(summary["brier_score"], float)
    assert summary["brier_score"] < 0.1
    assert len(summary["bins"]) >= 2
    assert "recommended" in summary
