import pandas as pd

from moneybot.services.temporal_validation import purged_embargoed_split


def test_purged_embargoed_split_uses_event_time_horizon_and_embargo():
    train = pd.DataFrame({"event_date": pd.date_range("2026-01-01", periods=10, freq="D").astype(str), "row": range(10)})
    test = pd.DataFrame({"event_date": pd.date_range("2026-01-11", periods=5, freq="D").astype(str), "row": range(10, 15)})

    purged_train, embargoed_test, diagnostics = purged_embargoed_split(train, test, horizon_days=5, embargo_days=1)

    assert purged_train["row"].tolist() == [0, 1, 2, 3, 4]
    assert embargoed_test["row"].tolist() == [11, 12, 13, 14]
    assert diagnostics["method"] == "event_time"
    assert diagnostics["purged_train_rows"] == 5
    assert diagnostics["embargoed_test_rows"] == 1
    assert diagnostics["label_horizon_gap_passed"] is True
    assert diagnostics["symbol_date_overlap_count"] == 0


def test_purged_embargoed_split_has_deterministic_row_fallback():
    train = pd.DataFrame({"row": range(10)})
    test = pd.DataFrame({"row": range(10, 15)})

    purged_train, embargoed_test, diagnostics = purged_embargoed_split(train, test, horizon_days=3, embargo_days=2)

    assert purged_train["row"].tolist() == list(range(7))
    assert embargoed_test["row"].tolist() == [12, 13, 14]
    assert diagnostics["method"] == "row_fallback"
