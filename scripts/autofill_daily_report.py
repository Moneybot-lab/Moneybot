#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from moneybot.services.runtime_paths import (
    day13_calibration_report_path,
    day13_recalibration_plan_path,
    decision_outcomes_snapshot_path,
    resolve_runtime_dir,
)


def _load_json(path: str) -> dict[str, Any]:
    file_path = Path(path)
    if not file_path.exists():
        return {}
    try:
        payload = json.loads(file_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _recent_git_changes(limit: int = 5) -> list[str]:
    try:
        output = subprocess.check_output(
            ["git", "log", "--oneline", f"-n{max(1, limit)}"],
            text=True,
        ).strip()
    except Exception:
        return []
    return [line.strip() for line in output.splitlines() if line.strip()]


def build_daily_report_markdown(
    *,
    summary: dict[str, Any],
    outcomes: dict[str, Any],
    calibration: dict[str, Any],
    plan: dict[str, Any],
    recent_changes: list[str],
) -> str:
    outcomes_data = outcomes.get("data") if isinstance(outcomes.get("data"), dict) else outcomes
    summary_1d = outcomes_data.get("summary_1d") if isinstance(outcomes_data.get("summary_1d"), dict) else {}
    summary_5d = outcomes_data.get("summary_5d") if isinstance(outcomes_data.get("summary_5d"), dict) else {}

    lines = [
        f"# Moneybot Daily Ops Report ({datetime.now(timezone.utc).date().isoformat()} UTC)",
        "",
        "## Decision Activity",
        f"- Events considered: {summary.get('events_considered', 0)}",
        f"- Source counts: {summary.get('source_counts', {})}",
        f"- Endpoint counts: {summary.get('endpoint_counts', {})}",
        "",
        "## Outcomes Snapshot",
        f"- 1D accuracy: {summary_1d.get('accuracy')} (evaluated rows: {summary_1d.get('evaluated_rows', 0)})",
        f"- 5D accuracy: {summary_5d.get('accuracy')} (evaluated rows: {summary_5d.get('evaluated_rows', 0)})",
        "",
        "## Calibration",
        f"- Rows: {calibration.get('rows')}",
        f"- Brier score: {calibration.get('brier_score')}",
        f"- Avg predicted: {calibration.get('avg_predicted')}",
        f"- Avg observed: {calibration.get('avg_observed')}",
        f"- Recommended delta: {calibration.get('recommended')}",
        "",
        "## Recalibration Plan",
        f"- Apply change: {plan.get('apply_change')}",
        f"- Current: {plan.get('current')}",
        f"- Next: {plan.get('next')}",
        f"- Recommended delta: {plan.get('recommended_delta')}",
        "",
        "## Recent Changes",
    ]
    if recent_changes:
        lines.extend([f"- {entry}" for entry in recent_changes])
    else:
        lines.append("- No git changes detected.")
    lines.append("")
    lines.append("## Push vs Local")
    lines.append("- **Push to GitHub**: code/scripts/docs/tests changes.")
    lines.append("- **Keep local**: `data/*.json`, `data/*.md`, runtime snapshots, logs.")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    base_dir = resolve_runtime_dir()
    parser = argparse.ArgumentParser(description="Generate a markdown daily ops report from current artifacts.")
    parser.add_argument("--summary", default=str(base_dir / "day7_decision_log_summary.json"))
    parser.add_argument("--outcomes", default=str(decision_outcomes_snapshot_path()))
    parser.add_argument("--calibration", default=str(day13_calibration_report_path()))
    parser.add_argument("--plan", default=str(day13_recalibration_plan_path()))
    parser.add_argument("--output", default=str(base_dir / "daily_report.md"))
    parser.add_argument("--git-limit", type=int, default=5)
    args = parser.parse_args()

    report_md = build_daily_report_markdown(
        summary=_load_json(args.summary),
        outcomes=_load_json(args.outcomes),
        calibration=_load_json(args.calibration),
        plan=_load_json(args.plan),
        recent_changes=_recent_git_changes(limit=args.git_limit),
    )
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(report_md, encoding="utf-8")
    print(report_md)


if __name__ == "__main__":
    main()
