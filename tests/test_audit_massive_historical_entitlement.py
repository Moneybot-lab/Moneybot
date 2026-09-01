import io
import json
import socket
import urllib.error

from scripts.audit_massive_historical_entitlement import (
    HARD_MAX_BYTES,
    HARD_MAX_REQUESTS,
    classify_http,
    run_audit,
    storage_estimates,
)


class Response:
    status = 200

    def __init__(self, payload):
        self.payload = json.dumps(payload).encode()

    def read(self, amount):
        return self.payload[:amount]


def test_dry_run_is_deterministic_and_performs_no_requests():
    calls = []
    kwargs = dict(
        execute=False,
        retrieved_at="2026-08-27T00:00:00+00:00",
        env={"MASSIVE_API_KEY": "do-not-print"},
        opener=lambda *args, **kw: calls.append(args),
    )
    first = run_audit(**kwargs)
    second = run_audit(**kwargs)
    assert first == second
    assert calls == []
    assert first["planned_probes"] == sorted(
        first["planned_probes"],
        key=lambda item: (item["dataset"], item["requested_date"]),
    )
    assert "do-not-print" not in json.dumps(first)


def test_hard_request_and_byte_caps_are_enforced():
    report = run_audit(
        execute=False,
        max_requests=999,
        max_bytes=999_999_999,
        retrieved_at="fixed",
    )
    assert report["probe_caps"]["max_requests"] == HARD_MAX_REQUESTS
    assert report["probe_caps"]["max_bytes"] == HARD_MAX_BYTES


def test_successful_probe_is_bounded_and_sanitized():
    requests = []

    def opener(request, timeout):
        requests.append((request, timeout))
        return Response({"results": [{"t": 1}]})

    report = run_audit(
        execute=True,
        max_requests=1,
        max_bytes=1024,
        env={"MASSIVE_API_KEY": "secret-value"},
        opener=opener,
        retrieved_at="fixed",
    )
    assert report["probe_results"][0]["classification"] == "ACCESS_CONFIRMED"
    assert report["probe_results"][0]["earliest_timestamp_returned"] == 1
    assert (
        report["probe_results"][0]["account_entitlement_conclusion"]
        == "access_confirmed_for_this_dataset_and_probe_only"
    )
    assert report["requests_attempted"] == report["requests_completed"] == 1
    assert report["bytes_received"] <= 1024
    assert "secret-value" not in json.dumps(report)


def test_error_classifications_are_distinct():
    assert classify_http(403, {}) == "ENTITLEMENT_DENIED"
    assert classify_http(401, {}) == "AUTHENTICATION_FAILED"
    assert classify_http(404, {}) == "DATA_UNAVAILABLE_FOR_DATE"
    assert classify_http(429, {}) == "RATE_LIMITED"
    assert classify_http(200, {"results": []}) == "EMPTY_VALID_RESPONSE"
    assert classify_http(503, {}) == "PROVIDER_ERROR"


def test_retry_limit_and_rate_limit_classification():
    calls = []

    def opener(request, timeout):
        calls.append(1)
        raise urllib.error.HTTPError("https://redacted", 429, "rate", {}, io.BytesIO())

    report = run_audit(
        execute=True,
        max_requests=3,
        max_retries=1,
        env={"MASSIVE_API_KEY": "secret"},
        opener=opener,
        retrieved_at="fixed",
    )
    assert len(calls) == report["requests_attempted"] == 3
    assert report["requests_attempted"] <= report["probe_caps"]["max_requests"]
    assert report["probe_results"][0]["classification"] == "RATE_LIMITED"


def test_timeout_classification_obeys_retry_limit():
    calls = []

    def opener(request, timeout):
        calls.append(timeout)
        raise socket.timeout()

    report = run_audit(
        execute=True,
        max_requests=1,
        max_retries=0,
        env={"MASSIVE_API_KEY": "secret"},
        opener=opener,
        retrieved_at="fixed",
    )
    assert calls == [15]
    assert report["probe_results"][0]["classification"] == "TIMEOUT"


def test_storage_estimates_use_stable_formulas_and_order():
    estimates = storage_estimates(trading_days=10, symbols=2)
    assert list(estimates) == sorted(estimates)
    assert estimates["daily_only"]["compressed_bytes"] == 10 * 2 * 32
    for values in estimates.values():
        assert values["peak_temporary_bytes"] == values["compressed_bytes"] * 6
        assert (
            values["recommended_with_backup_bytes"] == values["compressed_bytes"] * 12
        )


def test_retry_never_exceeds_global_request_cap():
    calls = []

    def opener(request, timeout):
        calls.append(1)
        raise urllib.error.HTTPError("https://redacted", 429, "rate", {}, io.BytesIO())

    report = run_audit(
        execute=True,
        max_requests=1,
        max_retries=2,
        env={"MASSIVE_API_KEY": "secret"},
        opener=opener,
        retrieved_at="fixed",
    )
    assert len(calls) == report["requests_attempted"] == 1
    assert report["requests_attempted"] <= report["probe_caps"]["max_requests"]


def test_byte_cap_stops_oversized_response_without_counting_download():
    report = run_audit(
        execute=True,
        max_requests=1,
        max_bytes=8,
        env={"MASSIVE_API_KEY": "secret"},
        opener=lambda request, timeout: Response({"results": [{"large": "payload"}]}),
        retrieved_at="fixed",
    )
    result = report["probe_results"][0]
    assert result["classification"] == "PROVIDER_ERROR"
    assert result["limitation"] == "byte_cap_exceeded"
    assert report["bytes_received"] == 0


def test_execute_without_credentials_makes_no_request():
    calls = []
    report = run_audit(
        execute=True,
        env={},
        opener=lambda *args, **kwargs: calls.append(1),
        retrieved_at="fixed",
    )
    assert calls == []
    assert report["execution_status"] == "AUTHENTICATION_FAILED"


def test_committed_inventory_json_is_deterministic_and_sanitized():
    from pathlib import Path

    path = Path("docs/reports/alpha_atlas_v4_massive_historical_inventory.json")
    payload = json.loads(path.read_text())
    assert payload["schema_version"] == "alpha-atlas-v4-massive-historical-inventory.v1"
    assert path.read_text() == json.dumps(payload, indent=2, sort_keys=True) + "\n"
    lowered = path.read_text().lower()
    assert "authorization" not in lowered
    assert "api_key" not in lowered
    assert "signed_url" not in lowered
