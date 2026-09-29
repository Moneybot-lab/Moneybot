#!/usr/bin/env python3
"""Validate corrected V4 acquisition amendment without external access."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

MIB = 1024 * 1024


def validate(amendment_path: Path, markdown_path: Path) -> dict[str, object]:
    amendment = json.loads(amendment_path.read_text(encoding="utf-8"))
    markdown = markdown_path.read_text(encoding="utf-8")
    requests = amendment["request_budget"]
    storage = amendment["storage_budget"]
    deps = {item["dependency"]: item for item in amendment["dependency_accounting"]}

    assert amendment["schema_version"].endswith(".v2")
    assert amendment["status"] == "CORRECTED_PREPARED_FOR_REVIEW_NOT_AUTHORIZED"
    assert amendment["corrects"]["commit"] == "1c2400d"
    assert set(deps) == {"universe_source", "typed_identity", "effective_dated_sector_mapping", "stock_spy_sector_history", "split_lineage", "corrections_and_refresh"}
    assert deps["universe_source"]["classification"].startswith("A_")
    assert deps["effective_dated_sector_mapping"]["endpoint"] is None

    assert requests["bootstrap"]["expected"] == 62 + 50 + 1 == 113
    assert requests["bootstrap"]["maximum"] == 62 * 3 + 50 * 3 + 5 * 3 == 351
    assert requests["per_session"]["expected"] == 62 + 50 + 1 == 113
    assert requests["per_session"]["maximum"] == 62 * 3 + 50 * 3 + 2 * 3 == 342
    assert requests["ten_sessions"] == {"expected": 1130, "maximum": 3420}
    assert requests["all_stages"]["expected"] == requests["operational_verification"]["expected"] + requests["bootstrap"]["expected"] + requests["ten_sessions"]["expected"]
    assert requests["all_stages"]["maximum"] == requests["operational_verification"]["maximum"] + requests["bootstrap"]["maximum"] + requests["ten_sessions"]["maximum"]
    assert requests["proposed_cap"] == requests["all_stages"]["maximum"] == 3789

    expected = storage["expected"]
    maximum = storage["maximum"]
    assert expected["primary_evidence_total_bytes"] == sum(expected[key] for key in ("verification_bytes", "bootstrap_bytes", "ten_session_acquisition_bytes", "ten_session_snapshots_bytes", "operational_manifests_bytes"))
    assert maximum["primary_evidence_total_bytes"] == sum(maximum[key] for key in ("verification_bytes", "bootstrap_bytes", "ten_session_acquisition_bytes", "ten_session_snapshots_bytes", "correction_revision_reserve_bytes", "operational_manifests_bytes"))
    assert expected["combined_retained_evidence_bytes"] == 2 * expected["primary_evidence_total_bytes"]
    assert maximum["combined_retained_evidence_bytes"] == 2 * maximum["primary_evidence_total_bytes"]
    caps = storage["caps"]
    assert maximum["primary_evidence_total_bytes"] <= caps["primary_uncompressed_evidence_bytes"]
    assert maximum["combined_retained_evidence_bytes"] <= caps["combined_uncompressed_evidence_bytes"]
    assert caps["required_primary_filesystem_capacity_bytes"] == sum(caps[key] for key in ("primary_uncompressed_evidence_bytes", "primary_temporary_staging_bytes", "restore_working_bytes", "filesystem_operating_headroom_bytes"))
    assert caps["required_primary_filesystem_capacity_bytes"] == 2048 * MIB
    assert caps["required_backup_capacity_bytes"] == 1024 * MIB

    requirements = {item["feature"]: item["valid_observations"] for item in amendment["lookback_contract"]["requirements"]}
    assert requirements["20-session return and 20-return volatility"] == 21
    assert requirements["20-return beta"] == 21
    assert requirements["SMA50 and price/SMA50"] == 50
    assert amendment["lookback_contract"]["not_a_guarantee"] is True
    assert amendment["adjustment_revision_policy"]["calculation_input"].startswith("Persist and calculate from provider adjusted=false")
    assert amendment["recommendation"]["acquisition_deadline"] == "07:30 America/New_York"
    assert amendment["storage_arrangement"]["operational_ready"] is False
    assert amendment["cost"]["affordability"] == "NOT_ESTABLISHED"
    assert amendment["universe"]["replacement_roster"] is None
    assert amendment["prohibitions_observed"]["provider_requests"] == 0

    for token in ("1,248", "3,789", "1,536 MiB", "07:30–07:45", "PROPOSED_NOT_APPROVED", "BLOCKED_STORAGE_AND_CACHE"):
        assert token in markdown

    return {"status":"PASS","expected_requests":requests["all_stages"]["expected"],"maximum_requests":requests["all_stages"]["maximum"],"maximum_retained_evidence_bytes":maximum["combined_retained_evidence_bytes"],"primary_capacity_bytes":caps["required_primary_filesystem_capacity_bytes"],"amendment_sha256":hashlib.sha256(amendment_path.read_bytes()).hexdigest(),"markdown_sha256":hashlib.sha256(markdown_path.read_bytes()).hexdigest()}


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--amendment", type=Path, default=root / "docs/reports/alpha_atlas_v4_prospective_data_acquisition_amendment.v2.json")
    parser.add_argument("--markdown", type=Path, default=root / "docs/reports/alpha_atlas_v4_prospective_data_acquisition_amendment.v2.md")
    args = parser.parse_args()
    print(json.dumps(validate(args.amendment, args.markdown), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
