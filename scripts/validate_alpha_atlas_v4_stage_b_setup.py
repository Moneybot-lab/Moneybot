"""Offline validator for the Stage B setup package; performs no network access."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "docs" / "reports"
MANIFEST = REPORTS / "alpha_atlas_v4_stage_b_verification_manifest.v1.json"
PACKAGE = REPORTS / "alpha_atlas_v4_stage_b_setup_package.v1.json"


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def validate() -> dict[str, object]:
    manifest = json.loads(MANIFEST.read_text())
    package = json.loads(PACKAGE.read_text())
    claimed = manifest.pop("content_sha256")
    actual = hashlib.sha256(canonical(manifest)).hexdigest()
    assert actual == claimed == package["manifest"]["sha256"]
    requests = manifest["requests_in_order"]
    assert [r["number"] for r in requests] == [1, 2, 3, 4, 5]
    assert sum(r["expected_attempts"] for r in requests) == 5
    assert sum(r["max_attempts"] for r in requests) == 18
    assert manifest["budget"]["maximum_attempts"] == 18
    assert manifest["budget"]["cumulative_ledger_cap"] == 3789
    assert manifest["history_window"]["count"] == 75
    assert manifest["stocks"] == ["AAPL"] and manifest["context_symbols"] == ["SPY", "XLK"]
    assert manifest["typed_identities"]["AAPL"]["security_id"] is None
    assert not manifest["sector_mapping"]["AAPL"]["covers_verification_session"]
    assert package["preserved_stage_a"]["live_provider_requests"] == 0
    assert package["preserved_stage_a"]["report_json_sha256"] == "ccd9a80839cd10dfe2beb1ad01b1e8779b010037c867923834b6bfa914726cb0"
    assert package["recommended_storage"]["primary"]["configured_size"] == "3 GB decimal"
    cost = package["cost"]
    expected = cost["render_disk"]["six_months"] + cost["s3_standard"]["six_month_storage"] + cost["s3_standard"]["one_time_put_max"] + cost["s3_standard"]["verification_restore_get_max"] + cost["s3_standard"]["max_egress"]
    assert abs(expected - cost["known_six_month_upper_arithmetic"]) < 1e-12
    assert package["status"] == "PREPARED_FOR_REVIEW_NOT_AUTHORIZED_NOT_EXECUTED"
    assert package["authorization_stages"]["measurements"]["status"] == "NOT_TESTED"
    assert any("NOT_IMPLEMENTED" in package["recommended_storage"]["adapter_status"] for _ in [0])
    return {"status": "PASS", "manifest_sha256": claimed, "expected_attempts": 5, "maximum_attempts": 18, "live_provider_requests": 0}


if __name__ == "__main__":
    print(json.dumps(validate(), indent=2, sort_keys=True))
