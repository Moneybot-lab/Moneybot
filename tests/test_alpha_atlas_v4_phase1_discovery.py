import json
import subprocess
import sys
from pathlib import Path

import pytest

from moneybot.services.alpha_atlas_v4_phase1_discovery import (
    DiscoveryResponse,
    build_discovery_reports,
    require_read_method,
    run_coverage_discovery,
    validate_limits,
)


def response(records, next_url=None, status=200):
    return DiscoveryResponse(
        status=status,
        body=json.dumps({"results": records, "next_url": next_url}).encode(),
        headers={},
    )


def test_static_discovery_writes_no_data_and_does_not_authorize_backfill():
    reports = run_coverage_discovery(live=False)
    assert reports["coverage_discovery"]["evidence_class"] == "STATIC_ONLY"
    assert reports["coverage_discovery"]["full_backfill_started"] is False
    assert reports["coverage_discovery"]["full_backfill_authorized"] is False
    assert reports["common_interval"]["proposed_start"] is None


@pytest.mark.parametrize(
    "name,value",
    [
        ("max_requests", 201),
        ("max_pages", 201),
        ("max_response_bytes", 5 * 1024 * 1024 + 1),
        ("max_total_bytes", 100 * 1024 * 1024 + 1),
        ("max_elapsed_seconds", 901),
        ("max_retries", 4),
        ("max_backoff_seconds", 31),
    ],
)
def test_hard_limits_are_enforced(name, value):
    with pytest.raises(ValueError, match=name):
        validate_limits({name: value})


def test_non_read_methods_are_rejected():
    assert require_read_method("get") == "GET"
    assert require_read_method("HEAD") == "HEAD"
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        with pytest.raises(ValueError, match="UNSAFE_HTTP_METHOD"):
            require_read_method(method)


def test_request_page_and_byte_limits_stop_cleanly():
    calls = []

    def fetch(method, url, headers, timeout):
        calls.append((method, url, headers, timeout))
        return response(
            [{"ticker": "A", "active": True}], next_url="https://api.massive.com/next"
        )

    reports = run_coverage_discovery(
        live=True,
        api_key="secret",
        limits={"max_requests": 1, "max_pages": 1},
        fetcher=fetch,
        clock=lambda: 0,
    )
    result = reports["coverage_discovery"]
    assert result["evidence_class"] == "PARTIAL_LIMIT_REACHED"
    assert result["stopped_by_limit"] == "max_requests"
    assert result["full_backfill_authorized"] is False
    assert "secret" not in json.dumps(reports)


def test_response_and_total_byte_limits_do_not_consume_partial_page():
    body = b"x" * 101

    def fetch(*_args, **_kwargs):
        return DiscoveryResponse(200, body, {})

    reports = run_coverage_discovery(
        live=True,
        api_key="secret",
        limits={"max_response_bytes": 100},
        fetcher=fetch,
        clock=lambda: 0,
    )
    assert reports["coverage_discovery"]["stopped_by_limit"] == "max_response_bytes"
    assert reports["coverage_discovery"]["records_enumerated"] == 0

    valid = response([{"ticker": "A", "active": True}])
    total_limited = run_coverage_discovery(
        live=True,
        api_key="secret",
        limits={
            "max_response_bytes": len(valid.body) + 1,
            "max_total_bytes": len(valid.body) - 1,
        },
        fetcher=lambda *_a, **_k: valid,
        clock=lambda: 0,
    )
    assert total_limited["coverage_discovery"]["stopped_by_limit"] == "max_total_bytes"


def test_runtime_limit_is_enforced():
    ticks = iter([0, 301, 301])
    reports = run_coverage_discovery(
        live=True,
        api_key="secret",
        limits={"max_elapsed_seconds": 300},
        fetcher=lambda *_a, **_k: response([]),
        clock=lambda: next(ticks),
    )
    assert reports["coverage_discovery"]["stopped_by_limit"] == "max_elapsed_seconds"


def test_rate_limit_retry_has_bounded_backoff():
    statuses = iter([429, 500, 200])
    sleeps = []

    def fetch(*_args):
        status = next(statuses)
        return response([], status=status)

    reports = run_coverage_discovery(
        live=True,
        api_key="secret",
        limits={"max_requests": 3, "max_retries": 2, "max_backoff_seconds": 2},
        fetcher=fetch,
        sleeper=sleeps.append,
        clock=lambda: 0,
    )
    assert sleeps == [1, 2]
    assert reports["coverage_discovery"]["metrics"]["retries"] == 2


def test_identity_conflicts_reused_tickers_and_sector_gaps_fail_closed():
    records = [
        {
            "ticker": "OLD",
            "active": False,
            "composite_figi": "ID1",
            "date": "2020-01-01",
        },
        {
            "ticker": "NEW",
            "active": True,
            "composite_figi": "ID1",
            "date": "2021-01-01",
        },
        {
            "ticker": "OLD",
            "active": True,
            "composite_figi": "ID2",
            "date": "2022-01-01",
        },
    ]
    reports = build_discovery_reports(
        records,
        evidence_class="LIVE_BOUNDED",
        metrics={"requests": 2},
        stopped_by="max_pages",
    )
    identity = reports["identity_coverage"]
    assert identity["reused_tickers"] == ["OLD"]
    assert identity["one_identity_many_tickers"] == ["ID1"]
    assert identity["conflicting_identity_mappings"] == 1
    assert identity["deterministic_point_in_time_chain"] is False
    assert reports["sector_coverage"]["effective_date_support"] is False
    assert reports["common_interval"]["proposed_start"] is None
    assert reports["terminal_price_discovery"]["final_policy_approved"] is False


def test_discovery_workflow_is_manual_and_cannot_backfill_or_train():
    text = Path(
        ".github/workflows/alpha-atlas-v4-phase1-coverage-discovery.yml"
    ).read_text()
    assert "workflow_dispatch:" in text and "schedule:" not in text
    assert "default: false" in text
    assert text.count("if: always()") == 2
    assert "concurrency:" in text and "timeout-minutes: 15" in text
    commands = "\n".join(
        line.strip() for line in text.splitlines() if line.strip().startswith("python ")
    )
    assert "discover_alpha_atlas_v4_phase1_coverage.py" in commands
    assert not any(
        word in commands for word in ("backfill", "train", "deploy", "promote")
    )


def test_static_cli_materializes_complete_safe_artifact_set(tmp_path):
    subprocess.run(
        [
            sys.executable,
            "scripts/discover_alpha_atlas_v4_phase1_coverage.py",
            "--output-dir",
            str(tmp_path),
        ],
        check=True,
    )
    expected = {
        "alpha_atlas_v4_phase1_coverage_discovery.json",
        "alpha_atlas_v4_phase1_coverage_discovery.md",
        "alpha_atlas_v4_phase1_identity_coverage.json",
        "alpha_atlas_v4_phase1_sector_coverage.json",
        "alpha_atlas_v4_phase1_common_interval.json",
        "alpha_atlas_v4_phase1_terminal_price_discovery.json",
        "alpha_atlas_v4_phase1_backfill_estimate.json",
        "alpha_atlas_v4_phase1_readiness.json",
        "alpha_atlas_v4_phase1_source_inventory.json",
        "alpha_atlas_v4_phase1_backfill_plan.json",
        "alpha_atlas_v4_phase1_coverage_discovery_workflow_summary.md",
    }
    assert expected <= {path.name for path in tmp_path.iterdir()}
    for name in expected:
        assert "secret" not in (tmp_path / name).read_text().lower()
    plan = json.loads(
        (tmp_path / "alpha_atlas_v4_phase1_backfill_plan.json").read_text()
    )
    assert plan["coverage_discovery_evidence"]["full_backfill_authorized"] is False
