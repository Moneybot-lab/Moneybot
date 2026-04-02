#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from moneybot.services.decision_log import summarize_decision_events


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _status(exists: bool) -> str:
    return "✅ Pass" if exists else "⚠️ Warning"


def _fmt(value: Any) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def build_report_text(
    *,
    day1_meta: dict[str, Any],
    decision_summary: dict[str, Any],
    outcomes_snapshot: dict[str, Any],
    calibration_report: dict[str, Any],
    recalibration_plan: dict[str, Any],
    now_utc: datetime,
) -> str:
    outcomes_data = outcomes_snapshot.get("data") if isinstance(outcomes_snapshot.get("data"), dict) else {}
    summary_1d = outcomes_data.get("summary_1d") if isinstance(outcomes_data.get("summary_1d"), dict) else {}
    summary_5d = outcomes_data.get("summary_5d") if isinstance(outcomes_data.get("summary_5d"), dict) else {}

    metrics = day1_meta.get("metrics") if isinstance(day1_meta.get("metrics"), dict) else {}

    latest_event = decision_summary.get("latest_event") if isinstance(decision_summary.get("latest_event"), dict) else {}
    endpoint_counts = decision_summary.get("endpoint_counts") if isinstance(decision_summary.get("endpoint_counts"), dict) else {}

    top_symbols = decision_summary.get("top_symbols")
    if isinstance(top_symbols, list):
        top_symbols_text = ", ".join(
            f"{item.get('symbol')} ({item.get('count')})"
            for item in top_symbols[:5]
            if isinstance(item, dict) and item.get("symbol") is not None
        ) or "N/A"
    else:
        top_symbols_text = "N/A"

    s1_counts = summary_1d.get("counts") if isinstance(summary_1d.get("counts"), dict) else {}
    s5_counts = summary_5d.get("counts") if isinstance(summary_5d.get("counts"), dict) else {}

    c_recommended = calibration_report.get("recommended") if isinstance(calibration_report.get("recommended"), dict) else {}
    r_current = recalibration_plan.get("current") if isinstance(recalibration_plan.get("current"), dict) else {}
    r_next = recalibration_plan.get("next") if isinstance(recalibration_plan.get("next"), dict) else {}

    return f"""# Moneybot Daily Terminal Report

**Date (UTC):** `{now_utc.date().isoformat()}`  
**Run started (UTC):** `HH:MM`  
**Run finished (UTC):** `HH:MM`

---

## 1) Artifact Refresh (Day 1)

- **Command:**
  ```bash
  python3 scripts/day1_refresh_artifact.py
  ```
- **Training rows written:** `{_fmt(day1_meta.get('train_rows'))}`
- **Model output path:** `{_fmt(day1_meta.get('model_path') or 'data/day1_baseline_model.json')}`
- **Metadata output path:** `data/day1_baseline_model.json.meta.json`
- **History output path:** `data/day1_baseline_model.json.history.json`
- **Baseline metrics:**
  - Accuracy: `{_fmt(metrics.get('accuracy'))}`
  - Positive rate: `{_fmt(metrics.get('positive_rate'))}`
  - Eval rows: `{_fmt(metrics.get('rows'))}`
- **Status:** {_status(bool(day1_meta))}
- **Notes:**
  - _Add any retries, API issues, or anomalies._

---

## 2) Decision Log Snapshot (Day 7)

- **Command:**
  ```bash
  python3 scripts/day7_decision_log_summary.py --input data/decision_events.jsonl --limit 200
  ```
- **Events considered:** `{_fmt(decision_summary.get('events_considered'))}`
- **Endpoint counts:**
  - `hot_momentum_buys`: `{_fmt(endpoint_counts.get('hot_momentum_buys'))}`
  - `quick_ask`: `{_fmt(endpoint_counts.get('quick_ask'))}`
  - `user_watchlist`: `{_fmt(endpoint_counts.get('user_watchlist'))}`
- **Top symbols:** `{top_symbols_text}`
- **Latest event:**
  - Symbol: `{_fmt(latest_event.get('symbol'))}`
  - Endpoint: `{_fmt(latest_event.get('endpoint'))}`
  - Decision source: `{_fmt(latest_event.get('decision_source'))}`
  - Timestamp (unix): `{_fmt(latest_event.get('ts'))}`
- **Status:** {_status(bool(decision_summary))}

---

## 3) Decision Outcome Evaluation (Day 9)

- **Command:**
  ```bash
  python3 scripts/day9_evaluate_decision_outcomes.py --input data/decision_events.jsonl --limit 200
  ```

### 1-Day Horizon Summary
- **Rows:** `{_fmt(summary_1d.get('rows'))}`
- **Evaluated rows:** `{_fmt(summary_1d.get('evaluated_rows'))}`
- **Accuracy:** `{_fmt(summary_1d.get('accuracy'))}`
- **Average return (1d):** `{_fmt(summary_1d.get('avg_return_1d'))}`
- **Counts:**
  - Correct: `{_fmt(s1_counts.get('correct'))}`
  - Incorrect: `{_fmt(s1_counts.get('incorrect'))}`
  - Neutral: `{_fmt(s1_counts.get('neutral'))}`
  - Skipped: `{_fmt(s1_counts.get('skipped'))}`

### 5-Day Horizon Summary
- **Rows:** `{_fmt(summary_5d.get('rows'))}`
- **Evaluated rows:** `{_fmt(summary_5d.get('evaluated_rows'))}`
- **Accuracy:** `{_fmt(summary_5d.get('accuracy'))}`
- **Average return (5d):** `{_fmt(summary_5d.get('avg_return_5d'))}`
- **Counts:**
  - Correct: `{_fmt(s5_counts.get('correct'))}`
  - Incorrect: `{_fmt(s5_counts.get('incorrect'))}`
  - Neutral: `{_fmt(s5_counts.get('neutral'))}`
  - Skipped: `{_fmt(s5_counts.get('skipped'))}`

- **Status:** {_status(bool(outcomes_snapshot))}

---

## 4) Outcomes Snapshot Materialization (Day 12)

- **Command:**
  ```bash
  python3 scripts/day12_materialize_outcomes.py --input data/decision_events.jsonl --output data/decision_outcomes_snapshot.json --limit 2000 --rows-limit 20
  ```
- **Output path:** `data/decision_outcomes_snapshot.json`
- **Rows materialized (if reported):** `{_fmt(len(outcomes_data.get('rows', [])) if isinstance(outcomes_data.get('rows'), list) else None)}`
- **Status:** {_status(bool(outcomes_snapshot))}

---

## 5) Calibration + Recalibration (Day 13)

- **Calibration command:**
  ```bash
  python3 scripts/day13_calibration_report.py --input data/decision_events.jsonl --output data/day13_calibration_report.json --limit 1000 --horizon-days 5
  ```
- **Recalibration command:**
  ```bash
  python3 scripts/day13_recalibrate.py --report data/day13_calibration_report.json --output data/day13_recalibration_plan.json --current-slope 1.0 --current-intercept 0.0
  ```

### Calibration report
- **Rows:** `{_fmt(calibration_report.get('rows'))}`
- **Avg predicted:** `{_fmt(calibration_report.get('avg_predicted'))}`
- **Avg observed:** `{_fmt(calibration_report.get('avg_observed'))}`
- **Brier score:** `{_fmt(calibration_report.get('brier_score'))}`
- **Recommended slope delta:** `{_fmt(c_recommended.get('slope_delta'))}`
- **Recommended intercept delta:** `{_fmt(c_recommended.get('intercept_delta'))}`

### Recalibration plan
- **Apply change:** `{_fmt(recalibration_plan.get('apply_change'))}`
- **Current slope/intercept:** `{_fmt(r_current.get('slope'))} / {_fmt(r_current.get('intercept'))}`
- **Next slope/intercept:** `{_fmt(r_next.get('slope'))} / {_fmt(r_next.get('intercept'))}`

- **Status:** {_status(bool(calibration_report) and bool(recalibration_plan))}

---

## 6) Overall Daily Summary

- **Pipeline health:** ✅ Healthy / ⚠️ Partial / ❌ Blocked
- **Key takeaways (2–5 bullets):**
  - `____`
  - `____`
  - `____`
- **Action items for next run:**
  - `____`
  - `____`
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a prefilled Markdown daily report from latest JSON outputs.")
    parser.add_argument("--model-meta", default="data/day1_baseline_model.json.meta.json")
    parser.add_argument("--decision-log", default="data/decision_events.jsonl")
    parser.add_argument("--decision-limit", type=int, default=200)
    parser.add_argument("--outcomes", default="data/decision_outcomes_snapshot.json")
    parser.add_argument("--calibration", default="data/day13_calibration_report.json")
    parser.add_argument("--recalibration", default="data/day13_recalibration_plan.json")
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    now_utc = datetime.now(timezone.utc)
    output_path = Path(args.output) if args.output else Path(f"docs/reports/daily_report_{now_utc.date().isoformat()}.md")

    day1_meta = _read_json(Path(args.model_meta))
    outcomes_snapshot = _read_json(Path(args.outcomes))
    calibration_report = _read_json(Path(args.calibration))
    recalibration_plan = _read_json(Path(args.recalibration))

    try:
        decision_summary = summarize_decision_events(args.decision_log, limit=max(1, args.decision_limit))
    except Exception:  # noqa: BLE001
        decision_summary = {}

    report = build_report_text(
        day1_meta=day1_meta,
        decision_summary=decision_summary,
        outcomes_snapshot=outcomes_snapshot,
        calibration_report=calibration_report,
        recalibration_plan=recalibration_plan,
        now_utc=now_utc,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report, encoding="utf-8")
    print(f"Wrote prefilled report -> {output_path}")


if __name__ == "__main__":
    main()
