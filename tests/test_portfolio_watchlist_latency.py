"""Full-route equivalence, provenance, authorization and event-count regressions."""

from concurrent.futures import ThreadPoolExecutor
import json
from unittest.mock import patch

import pytest
import requests

from scripts.benchmark_portfolio_watchlist import scenario, stable_payload
from moneybot.services.historical_evidence import project_prediction


@pytest.mark.parametrize("failure", ["timeout", "server_error"])
def test_retry_storm_repair_preserves_complete_portfolio(tmp_path, failure):
    results = []
    for repaired in (False, True):
        with scenario(tmp_path, 10, 5, failure, market_delay=0, ai_delay=0) as s:
            with patch.object(s.ai, "_portfolio_provider_cooldown",
                              s.ai._portfolio_provider_cooldown if repaired else lambda reason: None):
                s.log_path.unlink(missing_ok=True)
                response = s.client.get("/api/user-watchlist")
            assert response.status_code == 200
            results.append(stable_payload(response.get_json()))
            rows = response.get_json()["enriched_items"]
            assert len({row["id"] for row in rows}) == 10
            assert {row["symbol"] for row in rows} == {f"S{i:02}" for i in range(5)}
            total = sum((100 + i % 5) * (i + 1) for i in range(10))
            for row in rows:
                price = 100 + int(row["symbol"][1:])
                assert row["current_price"] == price
                assert row["performance_amount"] == round((price-row["entry_price"])*row["shares"], 2)
                assert row["position_weight_percent"] == round(price*row["shares"]/total*100, 2)
                assert row["history30"] == [price]*30
                assert all(bar["symbol"] == row["symbol"] for bar in row["history30_bars"])
                assert row["history30_source"] == row["quote_source"] == "massive"
                assert row["live_market"]["is_stale"]
                assert row["deterministic_portfolio"] is None
                assert row["ai_portfolio"]["mode"] == "rule_based"
                assert row["suitability"]["action"] == row["advice"]
            counts = s.m.stages
            assert counts["provider.ai.synthetic_openai"]["calls"] == (2 if repaired else 20)
            assert counts["provider.quote.synthetic_massive"]["calls"] == 5
            assert counts["provider.history.synthetic_massive"]["calls"] == 5
            assert counts["provider.sector.synthetic_yfinance"]["calls"] == 5
            assert counts["decision_logging"]["calls"] == 10
            events = [json.loads(line) for line in s.log_path.read_text().splitlines()]
            assert len(events) == 10
            for row, event in zip(rows, events):
                assert event["symbol"] == row["symbol"]
                assert event["decision_source"] == "rule_based"
                assert event.get("forecast") is None
                assert event["payload"]["probability_up"] is None
                assert event["snapshot"]["recommendation"] == row["advice"]
                assert event["snapshot"]["explanation"]["rationale"] == row["advice_reason"]
                assert event["snapshot"]["quote"]["is_stale"] is True
                assert event.get("ts")
                assert event["event_category"] == "portfolio_advice"
                projected, _ = project_prediction(event)
                assert projected is None
    assert results[0] == results[1]


def test_partial_market_failure_preserves_other_positions_and_entry_provenance(tmp_path):
    with scenario(tmp_path, 5, 5, "timeout", market_delay=0, ai_delay=0) as s:
        original = s.market.get_quote

        def quote(symbol):
            if symbol == "S02":
                raise requests.Timeout("synthetic quote timeout")
            return original(symbol)

        with patch.object(s.market, "get_quote", quote):
            response = s.client.get("/api/user-watchlist")
        assert response.status_code == 200
        for row in response.get_json()["enriched_items"]:
            if row["symbol"] == "S02":
                assert row["current_price"] == row["entry_price"]
                assert row["live_market"]["source"] == "portfolio_entry_price"
                assert row["live_market"]["is_stale"]
                assert row["quote_source"] is None
            else:
                assert row["current_price"] == 100+int(row["symbol"][1:])
                assert row["quote_source"] == "massive"
            assert len(row["history30"]) == 30


def test_authentication_skip_route_and_accounts_during_cooldown(tmp_path):
    with scenario(tmp_path, 5, 5, "timeout", market_delay=0, ai_delay=0) as s:
        assert s.app.test_client().get("/api/user-watchlist").status_code == 401
        assert s.m.stages["provider.ai.synthetic_openai"]["calls"] == 0
        skipped = s.client.get("/api/user-watchlist?skip_market_data=1").get_json()
        assert len(skipped["items"]) == 5 and skipped["enriched_items"] == []
        assert s.m.stages["provider.ai.synthetic_openai"]["calls"] == 0
        # Existing service-level health state may be shared, position data is not.
        s.ai._disabled_until = float("inf")
        clients = [s.client, s.app.test_client()]
        with clients[1].session_transaction() as session:
            session["user_id"] = 2
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda client: client.get("/api/user-watchlist"), clients))
        assert all(response.status_code == 200 for response in responses)
        first, second = [response.get_json()["enriched_items"] for response in responses]
        assert {row["symbol"] for row in first} == {f"S{i:02}" for i in range(5)}
        assert len(second) == 1 and second[0]["symbol"] == "S99"
        assert second[0]["entry_price"] == 50 and second[0]["shares"] == 3
        assert all(row["ai_portfolio"]["reason"] == "cooldown_after_failure" for row in first+second)
