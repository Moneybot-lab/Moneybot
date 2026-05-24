from moneybot.services.outcome_tracking import normalize_unix_ts


def test_normalize_unix_ts_accepts_numeric_inputs():
    assert normalize_unix_ts(1712345678) == 1712345678
    assert normalize_unix_ts(1712345678.9) == 1712345678
    assert normalize_unix_ts("1712345678") == 1712345678


def test_normalize_unix_ts_rejects_invalid_inputs():
    assert normalize_unix_ts(None) is None
    assert normalize_unix_ts(0) is None
    assert normalize_unix_ts(-1) is None
    assert normalize_unix_ts("") is None
    assert normalize_unix_ts("abc") is None
    assert normalize_unix_ts(True) is None
