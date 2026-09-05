import json
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

import pytest

from moneybot.services.alpha_atlas_v4_phase1 import (
    MAX_PROBES,
    authoritative_feature_mapping,
    classify_probe_payload,
    collapse_exact_records,
    comparison_metrics,
    controlled_backfill_plan,
    run_preflight,
    sanitize,
    source_inventory,
    validate_historical_reference,
)
from moneybot.services.market_data_providers import ExchangeCalendar


class Response:
    status = 200

    def __init__(self, payload):
        self.payload = payload

    def read(self, _limit):
        return json.dumps(self.payload).encode()


def test_source_inventory_schema_is_stable_and_complete():
    first = source_inventory()
    second = source_inventory()
    assert first == second
    assert first["schema_version"].endswith(".v1")
    required = {
        "daily_bars",
        "active_and_inactive_security_reference",
        "ticker_events",
        "splits",
        "dividends",
        "spy_context",
        "sector_etf_context",
        "exchange_calendar",
        "terminal_price_policy",
    }
    assert required == {x["source_id"] for x in first["sources"]}
    assert all(
        "backfill_readiness" in x and "point_in_time_capability" in x
        for x in first["sources"]
    )


def test_preflight_is_dry_by_default_and_missing_credentials_fail_closed():
    called = []
    dry = run_preflight(
        execute=False, env={}, opener=lambda *_a, **_k: called.append(1)
    )
    assert dry["full_backfill_started"] is False
    assert dry["write_operations"] == 0
    assert not called
    missing = run_preflight(execute=True, env={})
    assert missing["overall_status"] == "MISSING_CREDENTIALS"


def test_preflight_covers_representative_cases_and_classifies_incomplete():
    seen = []

    def opener(request, **_kwargs):
        seen.append(request.full_url)
        if "/reference/tickers/" in request.full_url:
            symbol = request.full_url.split("/reference/tickers/")[1].split("?")[0]
            return Response({"results": {"ticker": symbol}})
        if "/splits?" in request.full_url:
            return Response(
                {"results": [{"ticker": "AAPL", "execution_date": "2020-08-31"}]}
            )
        symbol = request.full_url.split("/ticker/")[1].split("/")[0]
        day = request.full_url.split("/day/")[1].split("/")[0]
        timestamp = int(
            datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp() * 1000
        )
        return Response(
            {
                "ticker": symbol,
                "results": [{"t": timestamp, "o": 1, "h": 2, "l": 1, "c": 2, "v": 10}],
            }
        )

    report = run_preflight(
        execute=True, env={"MASSIVE_API_KEY": "secret"}, opener=opener
    )
    assert report["overall_status"] == "COMPLETE"
    assert len(seen) <= MAX_PROBES
    cases = {p["case"] for p in report["probes"]}
    assert {
        "active_security",
        "delisted_security",
        "ticker_change",
        "split_case",
        "recent_listing",
        "spy_context",
        "sector_etf",
        "regular_session",
        "early_close",
        "old_period",
    } <= cases
    assert "secret" not in json.dumps(report)

    class TooLarge:
        def read(self, limit):
            return b"x" * (limit + 1)

    incomplete = run_preflight(
        execute=True,
        env={"MASSIVE_API_KEY": "secret"},
        opener=lambda *_a, **_k: TooLarge(),
    )
    assert incomplete["overall_status"] == "BLOCKED"
    assert {p["status"] for p in incomplete["probes"]} == {"INCOMPLETE_RESPONSE"}


def test_probe_schema_classifies_missing_incomplete_and_ambiguous():
    probe = {"symbol": "AAPL", "date": "2024-06-03", "family": "aggregate"}
    assert classify_probe_payload(probe, {"results": []})["status"] == "MISSING"
    assert (
        classify_probe_payload(
            probe, {"results": [{"ticker": "AAPL"}], "next_url": "opaque"}
        )["status"]
        == "INCOMPLETE_RESPONSE"
    )
    assert (
        classify_probe_payload(
            probe, {"results": [{"ticker": "AAPL"}], "resultsCount": 2}
        )["status"]
        == "INCOMPLETE_RESPONSE"
    )
    assert (
        classify_probe_payload(probe, {"ticker": "MSFT", "results": [{"t": 1}]})[
            "status"
        ]
        == "AMBIGUOUS"
    )


def test_inaccessible_source_and_sanitization():
    def denied(request, **_kwargs):
        raise urllib.error.HTTPError(request.full_url, 403, "apiKey=secret", {}, None)

    report = run_preflight(
        execute=True, env={"MASSIVE_API_KEY": "secret"}, opener=denied
    )
    assert report["overall_status"] == "BLOCKED"
    assert {p["status"] for p in report["probes"]} == {"INACCESSIBLE"}
    assert sanitize({"Authorization": "Bearer secret", "url": "?apiKey=secret"}) == {
        "url": "?apiKey=[REDACTED]"
    }


@pytest.mark.parametrize(
    "event_type",
    [
        "DELISTING",
        "TICKER_CHANGE",
        "MERGER",
        "ACQUISITION",
        "BANKRUPTCY",
        "NEW_LISTING",
    ],
)
def test_effective_dated_identity_events_are_accepted(event_type):
    record = {
        "event_type": event_type,
        "identity_class": "STABLE_SECURITY_ID",
        "metadata_semantics": "EFFECTIVE_DATED",
        "effective_from": "2020-01-01",
        "effective_to": "2020-12-31",
    }
    validate_historical_reference(record, as_of="2020-06-01")


def test_current_state_and_weak_identity_fail_closed():
    with pytest.raises(ValueError, match="CURRENT_STATE_REFERENCE_FORBIDDEN"):
        validate_historical_reference(
            {"metadata_semantics": "CURRENT_STATE", "effective_from": "2020-01-01"},
            as_of="2020-06-01",
        )
    with pytest.raises(ValueError, match="WEAK_IDENTITY"):
        validate_historical_reference(
            {
                "metadata_semantics": "EFFECTIVE_DATED",
                "effective_from": "2020-01-01",
                "identity_class": "REQUEST_EVENT_IDENTITY",
            },
            as_of="2020-06-01",
        )
    with pytest.raises(ValueError, match="REFERENCE_NOT_EFFECTIVE"):
        validate_historical_reference(
            {
                "metadata_semantics": "EFFECTIVE_DATED",
                "effective_from": "2021-01-01",
                "identity_class": "STABLE_SECURITY_ID",
            },
            as_of="2020-06-01",
        )


def test_calendar_regular_holiday_and_early_close_contract():
    calendar = ExchangeCalendar()
    assert calendar.is_trading_day(__import__("datetime").date(2024, 6, 3))
    assert not calendar.is_trading_day(__import__("datetime").date(2024, 7, 4))
    assert calendar.session_close(__import__("datetime").date(2024, 11, 29)).hour == 18


def test_authoritative_48_mapping_and_hash_are_deterministic():
    first = authoritative_feature_mapping()
    assert first == authoritative_feature_mapping()
    assert (
        first["model_input_count"],
        first["provenance_count"],
        first["total_count"],
    ) == (43, 5, 48)
    assert len(first["mapping_sha256"]) == 64
    assert [x["registry_position"] for x in first["features"]] == list(range(48))
    assert not any(
        x["training_inclusion"] != x["inference_inclusion"] for x in first["features"]
    )


def test_duplicate_collapse_weight_and_conflict_rejection():
    row = {
        "decision_id": "d1",
        "symbol": "A",
        "decision_at": "2020-01-01",
        "feature_cutoff_at": "2020-01-01",
        "label": 1,
        "probability": 0.8,
        "return": 0.1,
    }
    unique, report = collapse_exact_records([row, row, {**row, "decision_id": "d2"}])
    assert len(unique) == 2
    assert report == {
        "schema_version": "alpha-atlas-v4-phase1-duplicate-comparison.v1",
        "physical_records": 3,
        "unique_exact_records": 2,
        "exact_duplicates_collapsed": 1,
        "model_sample_weight": 1.0,
        "historical_records_modified": False,
    }
    assert comparison_metrics([row, row])["brier_score"] == pytest.approx(0.04)
    with pytest.raises(ValueError, match="CONFLICTING_IMMUTABLE_IDENTITY"):
        collapse_exact_records([row, {**row, "symbol": "B"}])


def test_backfill_plan_is_deterministic_blocked_and_does_not_execute():
    plan = controlled_backfill_plan()
    assert plan == controlled_backfill_plan()
    assert plan["execution_authorized"] is False
    assert plan["full_backfill_started"] is False
    assert plan["date_range"]["start"] is None
    assert "delisted coverage unverified" in plan["blocking_conditions"]
    assert plan["estimates"]["status"] == "PROVISIONAL"
    blocker_text = " ".join(plan["blocking_conditions"]).lower()
    assert all(
        forbidden not in blocker_text
        for forbidden in ("commercial", "license", "subscription", "business plan")
    )


def test_manual_preflight_workflow_contract():
    text = Path(".github/workflows/alpha-atlas-v4-phase1-preflight.yml").read_text()
    assert "workflow_dispatch:" in text
    assert "schedule:" not in text
    assert "MASSIVE_API_KEY: ${{ secrets.MASSIVE_API_KEY }}" in text
    assert "--execute-probes" in text
    assert "timeout-minutes: 15" in text
    assert "concurrency:" in text and "cancel-in-progress: false" in text
    assert "if: always()" in text
    assert "actions/upload-artifact@v4" in text
    assert "Missing required repository secret: MASSIVE_API_KEY" in text
    lowered = text.lower()
    assert "backfill" not in " ".join(
        line
        for line in lowered.splitlines()
        if line.lstrip().startswith(("python ", "python3 "))
    )
    assert not any(
        token in lowered for token in ("train_challenger", "deploy", "api_key=")
    )
