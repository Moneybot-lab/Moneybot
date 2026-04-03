# Moneybot Daily Terminal Report

**Date (UTC):** `YYYY-MM-DD`  
**Run started (UTC):** `HH:MM`  
**Run finished (UTC):** `HH:MM`

---

## 1) Artifact Refresh (Day 1)

- **Command:**
  ```bash
  python3 scripts/day1_refresh_artifact.py
  ```
- **Training rows written:** `____`
- **Model output path:** `data/day1_baseline_model.json`
- **Metadata output path:** `data/day1_baseline_model.json.meta.json`
- **History output path:** `data/day1_baseline_model.json.history.json`
- **Baseline metrics:**
  - Accuracy: `____`
  - Positive rate: `____`
  - Eval rows: `____`
- **Status:** ✅ Pass / ⚠️ Warning / ❌ Fail
- **Notes:**
  - _Add any retries, API issues, or anomalies._

---

## 2) Decision Log Snapshot (Day 7)

- **Command:**
  ```bash
  python3 scripts/day7_decision_log_summary.py --input data/decision_events.jsonl --limit 200
  ```
- **Events considered:** `____`
- **Endpoint counts:**
  - `hot_momentum_buys`: `____`
  - `quick_ask`: `____`
  - `user_watchlist`: `____`
- **Top symbols:** `____`
- **Latest event:**
  - Symbol: `____`
  - Endpoint: `____`
  - Decision source: `____`
  - Timestamp (unix): `____`
- **Status:** ✅ Pass / ⚠️ Warning / ❌ Fail

---

## 3) Decision Outcome Evaluation (Day 9)

- **Command:**
  ```bash
  python3 scripts/day9_evaluate_decision_outcomes.py --input data/decision_events.jsonl --limit 200
  ```

### 1-Day Horizon Summary
- **Rows:** `____`
- **Evaluated rows:** `____`
- **Accuracy:** `____`
- **Average return (1d):** `____`
- **Counts:**
  - Correct: `____`
  - Incorrect: `____`
  - Neutral: `____`
  - Skipped: `____`

### 5-Day Horizon Summary
- **Rows:** `____`
- **Evaluated rows:** `____`
- **Accuracy:** `____`
- **Average return (5d):** `____`
- **Counts:**
  - Correct: `____`
  - Incorrect: `____`
  - Neutral: `____`
  - Skipped: `____`

- **Status:** ✅ Pass / ⚠️ Warning / ❌ Fail

---

## 4) Outcomes Snapshot Materialization (Day 12)

- **Command:**
  ```bash
  python3 scripts/day12_materialize_outcomes.py --input data/decision_events.jsonl --output data/decision_outcomes_snapshot.json --limit 2000 --rows-limit 20
  ```
- **Output path:** `data/decision_outcomes_snapshot.json`
- **Rows materialized (if reported):** `____`
- **Status:** ✅ Pass / ⚠️ Warning / ❌ Fail

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
- **Rows:** `____`
- **Avg predicted:** `____`
- **Avg observed:** `____`
- **Brier score:** `____`
- **Recommended slope delta:** `____`
- **Recommended intercept delta:** `____`

### Recalibration plan
- **Apply change:** `true/false`
- **Current slope/intercept:** `____ / ____`
- **Next slope/intercept:** `____ / ____`

- **Status:** ✅ Pass / ⚠️ Warning / ❌ Fail

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

---

## Optional: Filled Example (2026-03-31)

> You can delete this section after your first use.

- Day 1 baseline accuracy: `0.4964`
- Day 9 1d directional accuracy: `0.7647` over `17` evaluated rows
- Day 13 calibration rows at 5d: `0` (no recalibration applied)

