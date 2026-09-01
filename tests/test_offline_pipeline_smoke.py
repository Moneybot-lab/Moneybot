import json
import hashlib
import os
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from moneybot.services.market_data_providers import ExchangeCalendar


def _ts(day: str) -> int:
    return int(
        datetime.fromisoformat(day).replace(hour=12, tzinfo=timezone.utc).timestamp()
    )


def _trading_days(start: date, count: int) -> list[date]:
    days = []
    candidate = start
    while len(days) < count:
        try:
            ExchangeCalendar().session_close(candidate)
        except ValueError:
            pass
        else:
            days.append(candidate)
        candidate += timedelta(days=1)
    return days


def _run(command: list[str], *, cwd: Path) -> None:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(cwd)
    completed = subprocess.run(
        command, cwd=cwd, env=env, capture_output=True, text=True, check=False
    )
    assert completed.returncode == 0, {
        "command": command,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def _run_failure(command: list[str], *, cwd: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(cwd)
    completed = subprocess.run(
        command, cwd=cwd, env=env, capture_output=True, text=True, check=False
    )
    assert completed.returncode != 0
    return completed


def test_massive_offline_training_pipeline_smoke(tmp_path):
    """Research smoke test stops after V4 canonicalization and feature materialization."""
    repo = Path(__file__).resolve().parents[1]
    raw_dir = tmp_path / "raw" / "2026-07-03" / "us_stocks_sip" / "day_aggs_v1"
    raw_dir.mkdir(parents=True)

    trading_days = _trading_days(date(2026, 1, 2), 100)
    for symbol, base in {"AAPL": 100.0, "MSFT": 200.0, "SPY": 400.0}.items():
        rows = ["ticker,date,open,high,low,close,volume"]
        for idx, trading_day in enumerate(trading_days, 1):
            day = trading_day.isoformat()
            close = base + (idx * 0.75) + ((idx % 7) * 0.2)
            rows.append(
                f"{symbol},{day},{close},{close + 1},{close - 1},{close},{1000000 + idx}"
            )
        (raw_dir / f"{symbol}.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")

    decision_log = tmp_path / "decision_events.jsonl"
    events = []
    for idx in range(57, 75):
        day = trading_days[idx - 1].isoformat()
        for symbol in ("AAPL", "MSFT"):
            events.append(
                {
                    "ts": _ts(day),
                    "endpoint": "quick_ask",
                    "symbol": symbol,
                    "decision_source": "deterministic_model",
                    "payload": {
                        "recommendation": "BUY" if idx % 3 else "HOLD",
                        "probability_up": 0.55,
                    },
                    "snapshot": {
                        "model_version": "smoke-production-v1",
                        "probability_up": 0.55,
                    },
                }
            )
    decision_log.write_text(
        "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8"
    )

    training_rows = tmp_path / "track_b" / "decision_training_snapshot_massive.jsonl"
    canonical_dir = tmp_path / "track_b" / "canonical"
    quality_dir = tmp_path / "track_b" / "training_quality"
    flat_dir = tmp_path / "track_b" / "flat_feature_store"
    phase0_dir = tmp_path / "track_b" / "phase0"

    split_cache = tmp_path / "massive_splits.jsonl"
    split_cache.write_text("", encoding="utf-8")
    (tmp_path / "split_adjustment_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "moneybot-corporate-actions.v1",
                "source_hash": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
                "corporate_action_normalization_passed": True,
            }
        ),
        encoding="utf-8",
    )
    _run(
        [
            sys.executable,
            "scripts/build_massive_decision_training_rows.py",
            "--raw-root",
            str(tmp_path / "raw"),
            "--decision-log",
            str(decision_log),
            "--output",
            str(training_rows),
            "--limit",
            "1000",
            "--horizon-days",
            "5",
            "--split-cache",
            str(split_cache),
            "--phase0-evidence-dir",
            str(phase0_dir / "source_evidence"),
            "--performance-output",
            str(phase0_dir / "builder_performance.json"),
        ],
        cwd=repo,
    )
    rejected = _run_failure(
        [
            sys.executable,
            "scripts/clean_training_snapshot.py",
            "--input",
            str(training_rows),
            "--output-dir",
            str(tmp_path / "must_not_clean_raw"),
        ],
        cwd=repo,
    )
    assert "Stale unadjusted Massive training snapshot" in rejected.stderr
    assert not (tmp_path / "must_not_clean_raw" / "cleaned_all.jsonl").exists()
    _run(
        [
            sys.executable,
            "scripts/canonicalize_alpha_atlas_v4_rows.py",
            "--input",
            str(training_rows),
            "--output-dir",
            str(canonical_dir),
        ],
        cwd=repo,
    )
    canonical_rows = canonical_dir / "canonical_observations.jsonl"
    _run(
        [
            sys.executable,
            "scripts/clean_training_snapshot.py",
            "--input",
            str(canonical_rows),
            "--output-dir",
            str(quality_dir),
            "--max-market-lag-days",
            "3",
            "--train-ratio",
            "0.8",
        ],
        cwd=repo,
    )
    _run(
        [
            sys.executable,
            "scripts/day15_materialize_flat_feature_store.py",
            "--input",
            str(quality_dir / "cleaned_all.jsonl"),
            "--output-dir",
            str(flat_dir),
            "--train-ratio",
            "0.8",
        ],
        cwd=repo,
    )
    _run(
        [
            sys.executable,
            "scripts/verify_alpha_atlas_v4_reconstructability.py",
            "--input",
            str(flat_dir / "all.jsonl"),
            "--root",
            str(phase0_dir / "source_evidence"),
            "--output",
            str(phase0_dir / "reconstructability_report.json"),
            "--certification-output",
            str(phase0_dir / "temporal_safety_certification.json"),
        ],
        cwd=repo,
    )
    split_dir = tmp_path / "track_b" / "challenger_suite"
    _run(
        [
            sys.executable,
            "scripts/plan_alpha_atlas_v4_temporal_split.py",
            "--input",
            str(flat_dir / "all.jsonl"),
            "--output-dir",
            str(split_dir),
            "--min-observations",
            "20",
        ],
        cwd=repo,
    )
    training_manifest = json.loads(
        training_rows.with_suffix(training_rows.suffix + ".manifest.json").read_text(
            encoding="utf-8"
        )
    )
    quality_report = json.loads(
        (quality_dir / "model_quality_report.json").read_text(encoding="utf-8")
    )
    cleaned_manifest = json.loads(
        (quality_dir / "cleaned_all.jsonl.manifest.json").read_text(encoding="utf-8")
    )
    feature_manifest = json.loads(
        (flat_dir / "manifest.json").read_text(encoding="utf-8")
    )
    assert (
        feature_manifest["feature_registry_version"]
        == "alpha-atlas-v4-feature-registry.v1"
    )
    assert len(feature_manifest["feature_columns"]) == 48
    assert len(feature_manifest["model_input_feature_columns"]) == 43
    assert set(feature_manifest["feature_columns"]) - set(
        feature_manifest["model_input_feature_columns"]
    ) == {
        "feature_cutoff_at",
        "feature_family_available_at",
        "feature_family_source_at",
        "feature_market_asof_date",
        "feature_split_ids",
    }
    split_plan = json.loads(
        (split_dir / "challenger_split_plan.json").read_text(encoding="utf-8")
    )
    split_diagnostics = json.loads(
        (split_dir / "challenger_split_diagnostics.json").read_text(encoding="utf-8")
    )
    canonical_diagnostics = json.loads(
        (canonical_dir / "canonicalization_diagnostics.json").read_text(
            encoding="utf-8"
        )
    )
    canonical_manifest = json.loads(
        canonical_rows.with_suffix(".jsonl.manifest.json").read_text(encoding="utf-8")
    )
    canonical_rows_payload = [
        json.loads(line) for line in canonical_rows.read_text().splitlines() if line
    ]

    assert "leakage_safe" not in training_manifest
    assert training_manifest["temporal_safety"]["status"] == "NOT_EVALUATED"
    assert training_manifest["temporal_safety"]["legacy_leakage_safe_accepted"] is False
    assert training_manifest["rows_joined"] >= 30
    evidence_manifest = json.loads(
        (phase0_dir / "source_evidence/source_evidence_manifest.json").read_text()
    )
    certification = json.loads(
        (phase0_dir / "temporal_safety_certification.json").read_text()
    )
    performance = json.loads((phase0_dir / "builder_performance.json").read_text())
    assert (
        evidence_manifest["schema_version"]
        == "alpha-atlas-v4-reconstruction-lineage.v1"
    )
    assert evidence_manifest["metrics"]["unique_source_objects"] == 3
    assert evidence_manifest["metrics"]["selected_source_rows"] > 100
    assert (
        evidence_manifest["metrics"]["raw_request_observations"]
        == training_manifest["rows_joined"]
    )
    assert evidence_manifest["metrics"]["canonical_economic_observations"] == 36
    assert certification["status"] == "VERIFIED_FOR_THIS_ARTIFACT"
    assert certification["rows_checked"] == certification["rows_total"]
    assert performance["status"] == "COMPLETED"
    assert performance["counters"]["source_objects_hashed"] == 3
    assert performance["counters"]["source_files_parsed"] == 3
    assert performance["counters"]["decision_events_loaded"] == 36
    assert {
        "decision_log_loading",
        "source_file_discovery",
        "source_object_hashing",
        "massive_daily_file_parsing",
        "decision_log_indexing",
        "market_history_indexing",
        "corporate_action_loading_and_indexing",
        "feature_construction",
        "source_window_selection",
        "source_row_identity_generation",
        "security_identity_evidence",
        "corporate_action_evidence",
        "lineage_accumulation_and_deduplication",
        "evidence_serialization",
        "raw_observation_serialization",
        "manifest_hashing",
        "manifest_finalization",
    }.issubset(performance["stage_seconds"])
    assert quality_report["training_ready"] is True
    assert quality_report["evaluation_ready"] is True
    assert (quality_dir / "cleaned_train.jsonl").exists()
    assert (quality_dir / "cleaned_test.jsonl").exists()
    assert feature_manifest["reproducibility"]["output_file_hashes"] is True
    assert split_plan["status"] == "FEASIBLE"
    assert split_plan["input_sha256"] == feature_manifest["files"][4]["sha256"]
    assert split_diagnostics["canonical_observations"] == feature_manifest["rows"]
    assert (split_dir / "challenger_train.jsonl").is_file()
    assert (split_dir / "challenger_test.jsonl").is_file()
    assert split_diagnostics["challenger_train.jsonl_sha256"]
    assert split_diagnostics["challenger_test.jsonl_sha256"]

    source_file = raw_dir / "AAPL.csv"
    original = source_file.read_bytes()
    source_file.write_bytes(original + b"\n")
    modified = _run_failure(
        [
            sys.executable,
            "scripts/verify_alpha_atlas_v4_reconstructability.py",
            "--input",
            str(flat_dir / "all.jsonl"),
            "--root",
            str(phase0_dir / "source_evidence"),
            "--output",
            str(phase0_dir / "modified_source_report.json"),
            "--certification-output",
            str(phase0_dir / "modified_source_certification.json"),
        ],
        cwd=repo,
    )
    assert "FAILED" in modified.stdout
    modified_report = json.loads(
        (phase0_dir / "modified_source_report.json").read_text()
    )
    assert any(
        key.startswith("source_object_hash_mismatch")
        for key in modified_report["failure_reasons"]
    )
    source_file.write_bytes(original)

    oversized = _run_failure(
        [
            sys.executable,
            "scripts/build_massive_decision_training_rows.py",
            "--raw-root",
            str(tmp_path / "raw"),
            "--decision-log",
            str(decision_log),
            "--output",
            str(tmp_path / "guarded/raw.jsonl"),
            "--split-cache",
            str(split_cache),
            "--phase0-evidence-dir",
            str(tmp_path / "guarded/evidence"),
            "--phase0-max-selected-rows",
            "1",
        ],
        cwd=repo,
    )
    assert "PHASE0_EVIDENCE_BUNDLE_TOO_LARGE:selected_row_limit" in oversized.stderr
    assert not (tmp_path / "guarded/raw.jsonl").exists()
    assert not (tmp_path / "guarded/raw.jsonl.manifest.json").exists()
    assert not (tmp_path / "guarded/evidence").exists()
    assert canonical_diagnostics["raw_request_rows"] >= 30
    assert canonical_diagnostics["canonical_observations"] >= 30
    assert (
        canonical_diagnostics["raw_request_rows"]
        >= canonical_diagnostics["canonical_observations"]
    )
    assert len(
        {row["canonical_observation_id"] for row in canonical_rows_payload}
    ) == len(canonical_rows_payload)
    assert {row["model_sample_weight"] for row in canonical_rows_payload} == {1.0}
    assert not any(
        key in row
        for row in canonical_rows_payload
        for key in (
            "feature_probability_up_delta_from_last_signal",
            "feature_previous_recommendation_buy",
            "feature_recommendation_changed",
            "feature_symbol_signal_count_7d",
            "feature_symbol_buy_count_7d",
            "feature_symbol_sell_count_7d",
            "feature_days_since_last_signal",
        )
    )
    assert (
        quality_report["canonical_input_sha256"]
        == canonical_manifest["canonical_observations_sha256"]
    )
    assert (
        cleaned_manifest["canonical_input_sha256"]
        == canonical_manifest["canonical_observations_sha256"]
    )
    assert (
        quality_report["canonical_input_schema_version"]
        == "alpha-atlas-v4-canonical-observations.v2"
    )
    assert (
        quality_report["split_metadata_hash"]
        == training_manifest["split_metadata_hash"]
    )
    assert (
        canonical_manifest["raw_input_sha256"]
        == hashlib.sha256(training_rows.read_bytes()).hexdigest()
    )
    assert not (tmp_path / "track_b" / "candidate_model_track_b.json").exists()
