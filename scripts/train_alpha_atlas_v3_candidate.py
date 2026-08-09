#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from moneybot.services.alpha_atlas_v3_features import (
    ALPHA_ATLAS_V3_FEATURES,
    FEATURE_CONTRACT_VERSION,
    FEATURE_ENGINE_VERSION,
    FORECAST_HORIZON,
    build_alpha_atlas_v3_features,
    ordered_feature_row,
    v3_feature_declarations,
)
from moneybot.services.deterministic_model import load_artifact, predict_proba
from moneybot.services.market_data import MarketDataService
from moneybot.services.production_servability import certify_candidate
from scripts.train_massive_baseline_model import train_massive_market_model

CANDIDATE_VERSION = "candidate-alpha-atlas-v3-clean-v1"
REPRESENTATIVE_SYMBOLS = (
    "AAPL",
    "SPY",
    "APLD",
    "UMAC",
    "ASPI",
    "ACHR",
    "JOBY",
    "LASE",
    "ONDS",
    "SQQQ",
)
TRANSFORM_CONTRACT = {
    "engine": FEATURE_ENGINE_VERSION,
    "bar_order": "ascending unique trading date",
    "adjustment": "provider adjusted daily bars",
    "return_units": "decimal",
    "ema": "alpha=2/(span+1), seeded with first close",
    "rsi": "14 simple-average gains/losses",
    "volatility": "population standard deviation of decimal returns",
    "missing": "fit-period median persisted in artifact",
}


def build_serving_dry_runs(
    *,
    artifact_path: Path,
    market_service: Any,
    symbols: tuple[str, ...] = REPRESENTATIVE_SYMBOLS,
) -> list[dict[str, Any]]:
    artifact = load_artifact(str(artifact_path))
    fills = (
        json.loads(artifact_path.read_text(encoding="utf-8")).get("feature_fill_values")
        or {}
    )
    spy = market_service.get_price_history_data("SPY", days=90)
    runs: list[dict[str, Any]] = []
    for symbol in symbols:
        history = (
            spy
            if symbol == "SPY"
            else market_service.get_price_history_data(symbol, days=90)
        )
        features = build_alpha_atlas_v3_features(
            symbol_bars=history.get("bars") or [],
            spy_bars=spy.get("bars") or [],
        )
        missing = [
            column
            for column in artifact.feature_columns
            if features.get(column) is None
        ]
        values = [
            (
                features.get(column)
                if features.get(column) is not None
                else fills.get(column)
            )
            for column in artifact.feature_columns
        ]
        usable = not missing and all(value is not None for value in values)
        probability = (
            float(predict_proba(artifact, np.asarray([values], dtype=float))[0])
            if usable
            else None
        )
        runs.append(
            {
                "symbol": symbol,
                "required_feature_count": len(artifact.feature_columns),
                "available_feature_count": len(artifact.feature_columns) - len(missing),
                "imputed_feature_count": len(missing),
                "missing_required_features": missing,
                "feature_contract_servable": usable,
                "raw_probability": probability,
                "forecast_horizon": FORECAST_HORIZON,
                "feature_vector_is_training_mean": bool(
                    usable
                    and np.allclose(np.asarray(values), np.asarray(artifact.means))
                ),
                "history_source": history.get("source"),
                "spy_history_source": spy.get("source"),
            }
        )
    return runs


def attach_v3_contract(
    candidate_path: Path, dry_runs: list[dict[str, Any]]
) -> dict[str, Any]:
    payload = json.loads(candidate_path.read_text(encoding="utf-8"))
    fills = payload.get("feature_fill_values") or {}
    columns = [str(column) for column in payload.get("feature_columns") or []]
    if columns != list(ALPHA_ATLAS_V3_FEATURES):
        raise ValueError(
            "Trained V3 candidate feature order does not match the canonical serving contract"
        )
    payload["production_feature_contract"] = {
        "feature_contract_version": FEATURE_CONTRACT_VERSION,
        "forecast_horizon": FORECAST_HORIZON,
        "lane": "decision",
        "leakage_safe": True,
        "feature_columns": columns,
        "required_features": columns,
        "optional_features": [],
        "features": v3_feature_declarations(),
        "training_transform": TRANSFORM_CONTRACT,
        "serving_transform": TRANSFORM_CONTRACT,
        "training_fill_policy": "fit_period_median",
        "serving_fill_policy": "fit_period_median",
        "fill_values": fills,
        "representative_dry_runs": dry_runs,
        "warnings": [],
    }
    payload["forecast_horizon"] = FORECAST_HORIZON
    payload["candidate_lane"] = "decision"
    candidate_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return payload


def train_v3_candidate(
    *,
    train_path: Path,
    test_path: Path,
    all_path: Path,
    output_dir: Path,
    market_service: Any,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    temporary = output_dir / ".candidate_alpha_atlas_v3_uncertified.json"
    final = output_dir / "candidate_alpha_atlas_v3_clean.json"
    report = train_massive_market_model(
        train_path,
        test_path,
        all_path,
        temporary,
        model_version=CANDIDATE_VERSION,
        report_prefix="alpha_atlas_v3",
        feature_allowlist=ALPHA_ATLAS_V3_FEATURES,
    )
    dry_runs = build_serving_dry_runs(
        artifact_path=temporary, market_service=market_service
    )
    dry_run_path = output_dir / "alpha_atlas_v3_representative_serving_dry_runs.json"
    dry_run_path.write_text(
        json.dumps(
            {
                "schema_version": "moneybot-alpha-atlas-v3-dry-run.v1",
                "feature_engine": FEATURE_ENGINE_VERSION,
                "forecast_horizon": FORECAST_HORIZON,
                "runs": dry_runs,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    attach_v3_contract(temporary, dry_runs)
    shutil.move(temporary, final)
    certification = certify_candidate(final)
    certification_path = output_dir / "production_servability_certification.json"
    certification_path.write_text(
        json.dumps(certification, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    recovery = {
        "schema_version": "moneybot-alpha-atlas-recovery-rebaseline.v1",
        "comparison_mode": "recovery_rebaseline",
        "baseline_version": "alpha-atlas-v2",
        "baseline_servability": "failed",
        "baseline_comparison_is_apples_to_apples": False,
        "candidate_version": CANDIDATE_VERSION,
        "candidate_servability": "passed" if certification["passed"] else "failed",
        "candidate_win": False,
        "automatic_promotion": False,
        "human_review_required": True,
        "candidate_metrics": report,
        "servability_certification": certification,
    }
    (output_dir / "alpha_atlas_v3_recovery_rebaseline_report.json").write_text(
        json.dumps(recovery, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    if not certification["passed"]:
        raise RuntimeError(
            "V3 candidate failed production servability certification: "
            + ", ".join(certification["blocking_reasons"])
        )
    return {
        "candidate_path": str(final),
        "certification_path": str(certification_path),
        "recovery_report_path": str(
            output_dir / "alpha_atlas_v3_recovery_rebaseline_report.json"
        ),
        "dry_run_report_path": str(dry_run_path),
        "training_report": report,
        "dry_runs": dry_runs,
        "certification": certification,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Train and certify the leakage-free Alpha Atlas V3 successor candidate without promotion."
    )
    parser.add_argument(
        "--train", default="data/track_b/training_quality/cleaned_train.jsonl"
    )
    parser.add_argument(
        "--test", default="data/track_b/training_quality/cleaned_test.jsonl"
    )
    parser.add_argument(
        "--all-cleaned", default="data/track_b/training_quality/cleaned_all.jsonl"
    )
    parser.add_argument("--output-dir", default="data/track_b/alpha_atlas_v3")
    args = parser.parse_args()
    result = train_v3_candidate(
        train_path=Path(args.train),
        test_path=Path(args.test),
        all_path=Path(args.all_cleaned),
        output_dir=Path(args.output_dir),
        market_service=MarketDataService(),
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
