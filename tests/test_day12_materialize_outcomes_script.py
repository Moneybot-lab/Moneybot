from scripts.day12_materialize_outcomes import select_visible_rows


def test_select_visible_rows_prefers_evaluated_rows_and_returns_list_slice():
    rows = [{"symbol": "RAW1"}, {"symbol": "RAW2"}]
    evaluated_rows = [{"symbol": "E1"}, {"symbol": "E2"}, {"symbol": "E3"}]

    visible = select_visible_rows(rows, evaluated_rows, 2)

    assert visible == [{"symbol": "E2"}, {"symbol": "E3"}]
    assert isinstance(visible, list)


def test_select_visible_rows_falls_back_to_recent_raw_rows():
    rows = [{"symbol": "RAW1"}, {"symbol": "RAW2"}, {"symbol": "RAW3"}]

    visible = select_visible_rows(rows, [], 2)

    assert visible == [{"symbol": "RAW2"}, {"symbol": "RAW3"}]
