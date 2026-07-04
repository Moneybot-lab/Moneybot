#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from moneybot.services.deterministic_advisor import LIVE_SUPPLIED_FEATURE_COLUMNS

PROMOTION_SCHEMA_VERSION = "moneybot-challenger-promotion.v1"


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _artifact_feature_columns(path: Path) -> list[str]:
    payload = _load_json(path)
    return [str(col) for col in payload.get("feature_columns") or []]


def _unsupported_live_features(path: Path) -> list[str]:
    return sorted(
        set(_artifact_feature_columns(path)).difference(LIVE_SUPPLIED_FEATURE_COLUMNS)
    )


def _eligible_challengers(report: dict[str, Any]) -> list[dict[str, Any]]:
    ranked = [str(item) for item in report.get("ranked_model_versions") or []]
    by_version = {
        str(item.get("model_version")): item
        for item in report.get("challengers") or []
        if isinstance(item, dict)
    }
    out: list[dict[str, Any]] = []
    for version in ranked:
        challenger = by_version.get(version)
        if not challenger:
            continue
        gates = (
            challenger.get("promotion_gates")
            if isinstance(challenger.get("promotion_gates"), dict)
            else {}
        )
        if gates.get("promotion_ready") is not True:
            continue
        if challenger.get("routing_allowed") is not False:
            continue
        if challenger.get("model_type") != "logistic_regression":
            continue
        out.append(challenger)
    return out


def prepare_challenger_promotion(
    *, backtest_report_path: Path, output_dir: Path
) -> dict[str, Any]:
    report = _load_json(backtest_report_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    comparison_path = output_dir / "model_comparison_track_b.json"
    candidate_path = output_dir / "candidate_model_track_b.json"
    eligible = _eligible_challengers(report)

    if not eligible:
        comparison = {
            "schema_version": PROMOTION_SCHEMA_VERSION,
            "candidate_win": False,
            "reasons": [
                "no logistic challenger cleared objective backtest, calibration, drawdown, benchmark, and drift gates"
            ],
            "backtest_report_path": str(backtest_report_path),
            "prepared_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        candidate_path.write_text(
            json.dumps(
                {"version": "no-promotable-challenger", "promotion_ready": False},
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
    else:
        selected = eligible[0]
        source_model = Path(str(selected["model_path"]))
        if not source_model.exists():
            raise FileNotFoundError(
                f"Selected challenger artifact not found: {source_model}"
            )
        unsupported = _unsupported_live_features(source_model)
        if unsupported:
            comparison = {
                "schema_version": PROMOTION_SCHEMA_VERSION,
                "candidate_win": False,
                "reasons": [
                    "selected challenger uses features not supplied by the live deterministic advisor",
                    f"unsupported_live_features: {', '.join(unsupported)}",
                ],
                "selected_model_version": selected.get("model_version"),
                "selected_model_type": selected.get("model_type"),
                "unsupported_live_features": unsupported,
                "live_supplied_feature_columns": sorted(LIVE_SUPPLIED_FEATURE_COLUMNS),
                "backtest_report_path": str(backtest_report_path),
                "prepared_at_utc": datetime.now(timezone.utc).isoformat(),
            }
            candidate_path.write_text(
                json.dumps(
                    {
                        "version": "no-live-compatible-challenger",
                        "promotion_ready": False,
                        "unsupported_live_features": unsupported,
                    },
                    indent=2,
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
        else:
            shutil.copy2(source_model, candidate_path)
            comparison = {
                "schema_version": PROMOTION_SCHEMA_VERSION,
                "candidate_win": True,
                "reasons": [
                    "challenger cleared objective chronological backtest gates",
                    "manual Render promotion still required",
                ],
                "selected_model_version": selected.get("model_version"),
                "selected_model_type": selected.get("model_type"),
                "candidate_metrics": selected.get("backtest_metrics"),
                "production_metrics": report.get("benchmark"),
                "promotion_gates": selected.get("promotion_gates"),
                "backtest_report_path": str(backtest_report_path),
                "live_supplied_feature_columns": sorted(LIVE_SUPPLIED_FEATURE_COLUMNS),
                "prepared_at_utc": datetime.now(timezone.utc).isoformat(),
            }

    comparison_path.write_text(
        json.dumps(comparison, indent=2, sort_keys=True), encoding="utf-8"
    )
    return {
        "comparison_report_path": str(comparison_path),
        "candidate_model_path": str(candidate_path),
        **comparison,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Prepare manual Render promotion artifacts from gated challenger backtests."
    )
    parser.add_argument(
        "--backtest-report", default="data/challenger_suite/backtest_report.json"
    )
    parser.add_argument("--output-dir", default="data/track_b")
    args = parser.parse_args()
    result = prepare_challenger_promotion(
        backtest_report_path=Path(args.backtest_report),
        output_dir=Path(args.output_dir),
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
