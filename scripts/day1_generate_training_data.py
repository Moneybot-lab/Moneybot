from __future__ import annotations

import argparse
import time
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd
import yfinance as yf
from yfinance.exceptions import YFRateLimitError

from moneybot.services.deterministic_model import attach_labels, engineer_features


DEFAULT_SYMBOLS = [
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "TSLA", "GOOGL", "NFLX", "AMD", "JPM",
]


def _fetch_history_with_retry(
    *,
    symbol: str,
    period: str,
    interval: str,
    max_retries: int,
    retry_delay_seconds: float,
) -> pd.DataFrame | None:
    attempts = max(1, int(max_retries))
    for attempt in range(1, attempts + 1):
        try:
            return yf.Ticker(symbol).history(period=period, interval=interval, auto_adjust=False)
        except YFRateLimitError:
            if attempt >= attempts:
                raise
            sleep_for = float(retry_delay_seconds) * attempt
            print(f"[{symbol}] yfinance rate-limited (attempt {attempt}/{attempts}). Sleeping {sleep_for:.1f}s...")
            time.sleep(max(0.1, sleep_for))
    return None


def build_snapshot(
    symbols: list[str],
    period: str,
    interval: str,
    horizon_days: int,
    target_return: float,
    *,
    max_retries: int,
    retry_delay_seconds: float,
    per_symbol_pause_seconds: float,
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for symbol in symbols:
        hist = _fetch_history_with_retry(
            symbol=symbol,
            period=period,
            interval=interval,
            max_retries=max_retries,
            retry_delay_seconds=retry_delay_seconds,
        )
        if hist is None or hist.empty:
            continue

        frame = hist[["Close", "Volume"]].copy()
        frame["symbol"] = symbol
        feats = engineer_features(frame)
        labeled = attach_labels(feats, horizon_days=horizon_days, target_return=target_return)
        labeled = labeled.reset_index().rename(columns={"Date": "timestamp"})
        frames.append(labeled)
        if per_symbol_pause_seconds > 0:
            time.sleep(per_symbol_pause_seconds)

    if not frames:
        raise RuntimeError("No historical data fetched; cannot build training snapshot.")

    out = pd.concat(frames, ignore_index=True)
    return out.sort_values(["symbol", "timestamp"])


def main() -> None:
    parser = argparse.ArgumentParser(description="Day-1 feature snapshot builder for deterministic baseline model")
    parser.add_argument("--symbols", nargs="*", default=DEFAULT_SYMBOLS)
    parser.add_argument("--period", default="2y", help="yfinance period, e.g. 1y, 2y")
    parser.add_argument("--interval", default="1d", help="yfinance interval, e.g. 1d")
    parser.add_argument("--horizon-days", type=int, default=5)
    parser.add_argument("--target-return", type=float, default=0.0)
    parser.add_argument("--output", default="data/day1_training_snapshot.csv")
    parser.add_argument("--max-retries", type=int, default=4, help="Retry attempts for per-symbol yfinance fetches.")
    parser.add_argument("--retry-delay-seconds", type=float, default=2.0, help="Base delay for rate-limit retries.")
    parser.add_argument(
        "--per-symbol-pause-seconds",
        type=float,
        default=0.4,
        help="Small pause between symbols to reduce rate-limit pressure.",
    )
    args = parser.parse_args()

    dataset = build_snapshot(
        symbols=[s.upper() for s in args.symbols],
        period=args.period,
        interval=args.interval,
        horizon_days=args.horizon_days,
        target_return=args.target_return,
        max_retries=args.max_retries,
        retry_delay_seconds=args.retry_delay_seconds,
        per_symbol_pause_seconds=args.per_symbol_pause_seconds,
    )
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    dataset.to_csv(output_path, index=False)

    print(f"Wrote {len(dataset)} rows to {output_path}")


if __name__ == "__main__":
    main()
