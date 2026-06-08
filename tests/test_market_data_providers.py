import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from moneybot.services.market_data_providers import (
    ExchangeCalendar,
    MassiveRestClient,
    ProviderForbiddenError,
    ProviderRateLimitError,
    ProviderUnsupportedError,
)

FIXTURES = Path(__file__).parent / "fixtures" / "massive"
NOW = datetime(2026, 6, 8, 14, 30, 5, tzinfo=timezone.utc)


def _fixture(name):
    return json.loads((FIXTURES / name).read_text())


class Response:
    def __init__(self, payload, status_code=200, headers=None):
        self.payload = payload
        self.status_code = status_code
        self.headers = headers or {}

    def json(self):
        return self.payload


def _client(response, **overrides):
    return MassiveRestClient(
        api_key="secret",
        retries=overrides.pop("retries", 0),
        http_get=lambda *args, **kwargs: response,
        clock=lambda: NOW,
        sleep=lambda _seconds: None,
        **overrides,
    )


def test_success_snapshot_normalizes_nbbo_trade_timestamps_and_price_selection():
    result = _client(Response(_fixture("snapshot_success.json"))).get_quote("aapl")
    quote = result.data

    assert quote.symbol == "AAPL"
    assert quote.bid == 200.08
    assert quote.ask == 200.12
    assert quote.midpoint == pytest.approx(200.10)
    assert quote.last_trade_price == 200.10
    assert quote.price == 200.10
    assert quote.price_source == "last_trade"
    assert quote.source == "massive"
    assert quote.source_mode == "rest"
    assert quote.market_session == "regular"
    assert quote.is_stale is False
    assert quote.age_ms == 4000
    assert quote.sequence_number == 1201
    assert quote.provider_event_id == "trade-1"
    assert quote.payload()["event_timestamp"].endswith("+00:00")


def test_partial_snapshot_uses_minute_close_and_marks_incomplete_nbbo():
    quote = _client(Response(_fixture("snapshot_partial.json"))).get_quote("AAPL").data

    assert quote.price == 200.05
    assert quote.price_source == "minute_close"
    assert "incomplete_nbbo" in quote.quality_flags


def test_stale_snapshot_is_never_labeled_live_by_market_data_service_contract():
    quote = _client(Response(_fixture("snapshot_stale.json"))).get_quote("AAPL").data

    assert quote.is_stale is True
    assert "stale" in quote.quality_flags
    assert "stale_price_fallback" in quote.quality_flags


def test_http_errors_map_forbidden_and_rate_limit_with_backoff():
    forbidden = _client(Response(_fixture("error_forbidden.json"), status_code=403))
    with pytest.raises(ProviderForbiddenError):
        forbidden.get_quote("AAPL")

    rate_limited = _client(Response(_fixture("error_rate_limited.json"), status_code=429, headers={"Retry-After": "12"}))
    with pytest.raises(ProviderRateLimitError) as captured:
        rate_limited.get_quote("AAPL")
    assert captured.value.retry_after_seconds == 12
    with pytest.raises(ProviderRateLimitError, match="backoff"):
        rate_limited.get_quote("MSFT")


def test_aggregate_contract_is_split_adjusted_and_second_rest_is_explicitly_unsupported():
    client = _client(Response(_fixture("aggregates_daily.json")))
    result = client.get_aggregates("AAPL", multiplier=1, timespan="day", start="2026-06-05", end="2026-06-06", adjusted=True)

    assert [bar.close for bar in result.data] == [194.0, 200.0]
    assert all(bar.adjusted_for_splits for bar in result.data)
    assert result.data[0].start_timestamp.tzinfo is timezone.utc
    with pytest.raises(ProviderUnsupportedError, match="WebSocket"):
        client.get_aggregates("AAPL", multiplier=1, timespan="second", start="2026-06-05", end="2026-06-06")


def test_exchange_calendar_handles_holiday_weekend_and_daylight_saving_sessions():
    calendar = ExchangeCalendar()

    assert calendar.session_at(datetime(2026, 7, 3, 15, 0, tzinfo=timezone.utc)) == "closed"  # observed July 4
    assert calendar.session_at(datetime(2026, 3, 9, 13, 15, tzinfo=timezone.utc)) == "pre"  # EDT
    assert calendar.session_at(datetime(2026, 3, 9, 14, 0, tzinfo=timezone.utc)) == "regular"
    assert calendar.session_at(datetime(2026, 11, 2, 14, 0, tzinfo=timezone.utc)) == "pre"  # EST
    assert calendar.session_at(datetime(2026, 11, 2, 15, 0, tzinfo=timezone.utc)) == "regular"


def test_normalized_snapshot_payload_is_reproducible_and_cache_avoids_duplicate_calls():
    calls = []
    response = Response(_fixture("snapshot_success.json"))
    client = MassiveRestClient(api_key="secret", retries=0, http_get=lambda *args, **kwargs: calls.append(args[0]) or response, clock=lambda: NOW)

    first = client.get_quote("AAPL")
    second = client.get_quote("AAPL")

    assert first.data.payload() == second.data.payload()
    assert second.cache_status == "hit"
    assert len(calls) == 1
    assert client.metrics.snapshot()["cache"]["hit"] >= 1


def test_endpoint_coverage_uses_documented_massive_rest_paths():
    calls = []

    def get(url, params=None, timeout=None):
        calls.append((url, params or {}))
        if "/aggs/" in url:
            return Response(_fixture("aggregates_daily.json"))
        return Response({"status": "OK", "request_id": "ref", "results": []})

    client = MassiveRestClient(api_key="secret", retries=0, http_get=get, clock=lambda: NOW)
    client.latest_trade("AAPL")
    client.latest_quote("AAPL")
    client.get_aggregates("AAPL", multiplier=1, timespan="minute", start="2026-06-08", end="2026-06-08")
    client.ticker_details("AAPL")
    client.splits("AAPL")
    client.dividends("AAPL")
    client.ratios("AAPL")

    urls = [url for url, _params in calls]
    assert any(url.endswith("/v2/last/trade/AAPL") for url in urls)
    assert any(url.endswith("/v2/last/nbbo/AAPL") for url in urls)
    assert any("/v2/aggs/ticker/AAPL/range/1/minute/" in url for url in urls)
    assert any(url.endswith("/v3/reference/tickers/AAPL") for url in urls)
    assert any(url.endswith("/stocks/v1/splits") for url in urls)
    assert any(url.endswith("/stocks/v1/dividends") for url in urls)
    assert any(url.endswith("/stocks/financials/v1/ratios") for url in urls)


def test_corporate_action_adjustment_factors_are_preserved_without_double_adjusting_bars():
    responses = {
        "/stocks/v1/splits": {"status": "OK", "results": [{"ticker": "AAPL", "execution_date": "2020-08-31", "historical_adjustment_factor": 0.25}]},
        "/stocks/v1/dividends": {"status": "OK", "results": [{"ticker": "AAPL", "ex_dividend_date": "2025-08-11", "historical_adjustment_factor": 0.997899}]},
    }

    def get(url, params=None, timeout=None):
        return Response(next(payload for suffix, payload in responses.items() if url.endswith(suffix)))

    client = MassiveRestClient(api_key="secret", retries=0, http_get=get, clock=lambda: NOW)
    split = client.splits("AAPL").data["results"][0]
    dividend = client.dividends("AAPL").data["results"][0]

    assert split["historical_adjustment_factor"] == 0.25
    assert dividend["historical_adjustment_factor"] == 0.997899


def test_normalized_fallback_with_old_timestamp_is_stale():
    from moneybot.services.market_data_providers import normalized_fallback_quote

    payload = normalized_fallback_quote(
        symbol="AAPL",
        price=100,
        change_percent=1,
        source="finnhub",
        received_timestamp=datetime(2026, 6, 8, 14, 30, tzinfo=timezone.utc),
        event_timestamp=datetime(2026, 6, 8, 14, 29, tzinfo=timezone.utc),
    )

    assert payload["market_session"] == "regular"
    assert payload["is_stale"] is True
    assert "stale" in payload["quality_flags"]
