# Moneybot Daily Terminal Report

**Date (UTC):** `2026-04-01`  
**Run started (UTC):** `HH:MM`  
**Run finished (UTC):** `HH:MM`

---

## 1) Artifact Refresh (Day 1)

- **Command:**
  ```bash
  python3 scripts/day1_refresh_artifact.py
  ```
- **Training rows written:** `3858`
- **Model output path:** `data/day1_baseline_model.json`
- **Metadata output path:** `data/day1_baseline_model.json.meta.json`
- **History output path:** `data/day1_baseline_model.json.history.json`
- **Baseline metrics:**
  - Accuracy: `0.5057`
  - Positive rate: `0.2249`
  - Eval rows: `965.0000`
- **Status:** ✅ Pass
- **Notes:**
  - _Add any retries, API issues, or anomalies._

---

## 2) Decision Log Snapshot (Day 7)

- **Command:**
  ```bash
  python3 scripts/day7_decision_log_summary.py --input data/decision_events.jsonl --limit 200
  ```
- **Events considered:** `104`
- **Endpoint counts:**
  - `hot_momentum_buys`: `35`
  - `quick_ask`: `3`
  - `user_watchlist`: `66`
- **Top symbols:** `F (16), NIO (15), SOUN (14), AAPL (9), APLD (9)`
- **Latest event:**
  - Symbol: `F`
  - Endpoint: `hot_momentum_buys`
  - Decision source: `deterministic_model`
  - Timestamp (unix): `1774752427`
- **Status:** ✅ Pass

---

## 3) Decision Outcome Evaluation (Day 9)

- **Command:**
  ```bash
  python3 scripts/day9_evaluate_decision_outcomes.py --input data/decision_events.jsonl --limit 200
  ```

### 1-Day Horizon Summary
- **Rows:** `20`
- **Evaluated rows:** `5`
- **Accuracy:** `0.6000`
- **Average return (1d):** `0.0071`
- **Counts:**
  - Correct: `3`
  - Incorrect: `2`
  - Neutral: `15`
  - Skipped: `0`

### 5-Day Horizon Summary
- **Rows:** `20`
- **Evaluated rows:** `0`
- **Accuracy:** `N/A`
- **Average return (5d):** `N/A`
- **Counts:**
  - Correct: `0`
  - Incorrect: `0`
  - Neutral: `0`
  - Skipped: `20`

- **Status:** ✅ Pass

---

## 4) Outcomes Snapshot Materialization (Day 12)

- **Command:**
  ```bash
  python3 scripts/day12_materialize_outcomes.py --input data/decision_events.jsonl --output data/decision_outcomes_snapshot.json --limit 2000 --rows-limit 20
  ```
- **Output path:** `data/decision_outcomes_snapshot.json`
- **Rows materialized (if reported):** `20`
- **Status:** ✅ Pass

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
- **Rows:** `5`
- **Avg predicted:** `0.5488`
- **Avg observed:** `0.4000`
- **Brier score:** `0.2610`
- **Recommended slope delta:** `0.0000`
- **Recommended intercept delta:** `-0.6011`

### Recalibration plan
- **Apply change:** `False`
- **Current slope/intercept:** `1.0000 / 0.0000`
- **Next slope/intercept:** `1.0000 / 0.0000`

- **Status:** ✅ Pass

---

## 6) Overall Daily Summary

- **Pipeline health:** ✅ Healthy / ⚠️ Partial / ❌ Blocked
- **Key takeaways (2–5 bullets):**
  - `Model performance is slightly above random but not yet statistically reliable`
  - `Only 5 evaluated outcomes (1-day) and 0 for 5-day → you don’t have enough closed trades to validate anything meaningful yet.
`
  - `System is functioning correctly end-to-end`
  - `Avg predicted: 0.5488 Avg observed: 0.4000 Your model is too optimistic, even though recalibration didn’t trigger yet.`
- **Action items for next run:**
  - `Increase outcome sample size ASAP`
  - `Implement soft calibration adjustment (don’t wait for threshold)`
  - `Audit “Neutral” outcomes logic`
  - `Start tracking per-symbol performance`