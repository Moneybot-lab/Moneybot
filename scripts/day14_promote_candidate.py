#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from moneybot.services.model_metadata import append_artifact_history, save_artifact_metadata


ALPHA_ATLAS_VERSION_RE = re.compile(r"^alpha-atlas-v(?P<number>\d+)$", re.IGNORECASE)


def _alpha_atlas_version_number(version: str | None) -> int | None:
    if not version:
        return None
    match = ALPHA_ATLAS_VERSION_RE.match(str(version).strip())
    if not match:
        return None
    return int(match.group("number"))


def _next_alpha_atlas_version(existing_version: str | None) -> str:
    number = _alpha_atlas_version_number(existing_version)
    if number is None:
        number = 1
    return f"alpha-atlas-v{number + 1}"


def _load_json(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def main() -> None:
    parser = argparse.ArgumentParser(description="Promote candidate model to production based on comparison report.")
    parser.add_argument("--comparison-report", default="data/model_comparison_report.json")
    parser.add_argument("--candidate-model", default="data/candidate_model.json")
    parser.add_argument("--production-model", default="data/day1_baseline_model.json")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    report = _load_json(args.comparison_report)
    candidate_win = bool(report.get("candidate_win"))
    if not candidate_win and not args.force:
        print("no promotion")
        return

    candidate_path = Path(args.candidate_model)
    production_path = Path(args.production_model)
    if not candidate_path.exists():
        raise SystemExit(f"Candidate model not found: {candidate_path}")
    candidate_model = _load_json(str(candidate_path))
    candidate_version = str(candidate_model.get("version") or candidate_model.get("model_version") or "").strip()
    if candidate_model.get("promotion_ready") is False or candidate_version == "no-promotable-challenger":
        raise SystemExit("Candidate model is an explicit no-promotion placeholder; refusing promotion even with --force.")

    existing_production = _load_json(str(production_path))
    existing_version = str(existing_production.get("version") or existing_production.get("model_version") or "").strip()
    promoted_version = _next_alpha_atlas_version(existing_version)
    candidate_model["version"] = promoted_version
    candidate_model["source_candidate_version"] = candidate_version or None

    production_path.parent.mkdir(parents=True, exist_ok=True)
    if production_path.exists():
        backup_path = production_path.with_suffix(production_path.suffix + ".bak")
        shutil.copy2(production_path, backup_path)

    tmp_path = production_path.with_suffix(production_path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(candidate_model, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    tmp_path.replace(production_path)

    metadata = {
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "model_path": str(production_path),
        "model_version": promoted_version,
        "source_candidate_version": candidate_version or None,
        "input_path": str(args.comparison_report),
        "train_rows": int((report.get("candidate_metrics") or {}).get("rows") or 0),
        "test_rows": int((report.get("production_metrics") or {}).get("rows") or 0),
        "metrics": {
            "candidate": report.get("candidate_metrics"),
            "production": report.get("production_metrics"),
        },
        "train_ratio": 0.8,
        "horizon_days": 5,
        "target_return": 0.0,
        "promotion_reason": "forced" if args.force and not candidate_win else "candidate_win",
    }
    metadata_path = save_artifact_metadata(str(production_path), metadata)
    history_path = append_artifact_history(str(production_path), metadata)
    print(f"promoted candidate -> {production_path}")
    print(f"Saved metadata -> {metadata_path}")
    print(f"Updated history -> {history_path}")


if __name__ == "__main__":
    main()
