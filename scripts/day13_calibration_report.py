#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import yfinance as yf

from moneybot.services.decision_log import read_decision_events
from moneybot.services.outcome_tracking import close_values


def _future_return(symbol: str, start_ts: int, days: int) -> float | None:
    start_dt = datetime.fromtimestamp(int(start_ts), tz=timezone.utc)
    end_dt = start_dt + timedelta(days=max(days + 3, 7))
    history = yf.download(
        symbol,
        start=start_dt.strftime("%Y-%m-%d"),
        end=end_dt.strftime("%Y-%m-%d"),
        interval="1d",
        progress=False,
        auto_adjust=False,
    )
    closes = close_values(history)
    if len(closes) <= days:
        return None
    start_price = float(closes[0])
    end_price = float(closes[days])
    if start_price == 0:
        return None
    return (end_price - start_price) / start_price


def calibration_rows_from_events(events: list[dict], *, horizon_days: int = 5, min_prob: float = 0.0) -> list[dict]:
    out: list[dict] = []
    for event in events:
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        prob = payload.get("probability_up")
        if not isinstance(prob, (int, float)):
            continue
        prob_f = float(prob)
        if prob_f < min_prob:
            continue
        symbol = str(event.get("symbol") or "").strip().upper()
        ts = event.get("ts")
        if not symbol or not isinstance(ts, int):
            continue
        future_ret = _future_return(symbol, ts, horizon_days)
        if future_ret is None:
            continue
        out.append(
            {
                "symbol": symbol,
                "ts": ts,
                "predicted": max(0.0, min(1.0, prob_f)),
                "observed": 1.0 if future_ret > 0 else 0.0,
            }
        )
    return out


def calibration_summary(rows: list[dict], *, bins: int = 10) -> dict:
    if not rows:
        return {
            "rows": 0,
            "brier_score": None,
            "avg_predicted": None,
            "avg_observed": None,
            "bins": [],
            "recommended": {"intercept_delta": 0.0, "slope_delta": 0.0},
        }

    brier = sum((r["predicted"] - r["observed"]) ** 2 for r in rows) / len(rows)
    avg_pred = sum(r["predicted"] for r in rows) / len(rows)
    avg_obs = sum(r["observed"] for r in rows) / len(rows)

    bucket_rows: list[list[dict]] = [[] for _ in range(max(1, bins))]
    for row in rows:
        idx = min(int(row["predicted"] * bins), bins - 1)
        bucket_rows[idx].append(row)

    bucket_summary: list[dict] = []
    for idx, bucket in enumerate(bucket_rows):
        if not bucket:
            continue
        low = idx / bins
        high = (idx + 1) / bins
        bucket_summary.append(
            {
                "range": [round(low, 2), round(high, 2)],
                "count": len(bucket),
                "avg_predicted": round(sum(r["predicted"] for r in bucket) / len(bucket), 4),
                "avg_observed": round(sum(r["observed"] for r in bucket) / len(bucket), 4),
            }
        )

    pred_safe = min(max(avg_pred, 1e-6), 1.0 - 1e-6)
    obs_safe = min(max(avg_obs, 1e-6), 1.0 - 1e-6)
    intercept_delta = math.log(obs_safe / (1.0 - obs_safe)) - math.log(pred_safe / (1.0 - pred_safe))
    slope_delta = 0.0

    return {
        "rows": len(rows),
        "brier_score": round(float(brier), 6),
        "avg_predicted": round(float(avg_pred), 6),
        "avg_observed": round(float(avg_obs), 6),
        "bins": bucket_summary,
        "recommended": {
            "intercept_delta": round(float(intercept_delta), 6),
            "slope_delta": round(float(slope_delta), 6),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Day-13 deterministic calibration diagnostics report.")
    parser.add_argument("--input", default="data/decision_events.jsonl")
    parser.add_argument("--output", default="data/day13_calibration_report.json")
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--horizon-days", type=int, default=5)
    parser.add_argument("--bins", type=int, default=10)
    args = parser.parse_args()

    events = read_decision_events(args.input, limit=max(1, args.limit))
    rows = calibration_rows_from_events(events, horizon_days=max(1, args.horizon_days))
    summary = calibration_summary(rows, bins=max(2, args.bins))
    payload = {
        "schema_version": "calibration_report.v1",
        "computed_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_path": args.input,
        "horizon_days": max(1, args.horizon_days),
        **summary,
    }
    serialized = json.dumps(payload, indent=2, sort_keys=True)
    print(serialized)
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(serialized, encoding="utf-8")


if __name__ == "__main__":
    main()
