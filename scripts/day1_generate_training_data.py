from __future__ import annotations

import argparse
import time
from datetime import datetime, timezone
from pathlib import Path
import sys
from urllib.parse import quote_plus
from xml.etree import ElementTree as ET


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd
import requests
import yfinance as yf
from yfinance.exceptions import YFRateLimitError

from moneybot.services.deterministic_model import attach_labels, engineer_features


DEFAULT_SYMBOLS = [
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "TSLA", "GOOGL", "NFLX", "AMD", "JPM",
]


def _source_weight(publisher: str) -> float:
    source = str(publisher or "").lower()
    if "reuters" in source:
        return 1.0
    if "bloomberg" in source:
        return 0.97
    if "wall street journal" in source:
        return 0.95
    if "financial times" in source:
        return 0.95
    if "cnbc" in source:
        return 0.93
    if "yahoo" in source:
        return 0.88
    return 0.75


def _headline_sentiment(title: str) -> float:
    text = str(title or "").lower()
    positive = {"beats", "upgrade", "surge", "rally", "wins", "growth", "record", "expands"}
    negative = {"misses", "downgrade", "lawsuit", "plunge", "drop", "falls", "loss", "delay", "war"}
    pos = sum(1 for token in positive if token in text)
    neg = sum(1 for token in negative if token in text)
    if pos == 0 and neg == 0:
        return 0.0
    return max(-1.0, min(1.0, (pos - neg) / max(1, pos + neg)))


def _google_news_events(symbol: str, timeout_s: float = 8.0) -> list[dict]:
    query = quote_plus(f"{symbol} stock")
    url = f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
    try:
        resp = requests.get(url, timeout=timeout_s)
        resp.raise_for_status()
        root = ET.fromstring(resp.text)
        events: list[dict] = []
        for item in root.findall(".//channel/item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            pub_date = (item.findtext("pubDate") or "").strip()
            source_node = item.find("source")
            publisher = (source_node.text or "").strip() if source_node is not None else "Google News"
            if not title:
                continue
            published_at = None
            if pub_date:
                try:
                    published_at = datetime.strptime(pub_date, "%a, %d %b %Y %H:%M:%S %Z").replace(tzinfo=timezone.utc)
                except Exception:  # noqa: BLE001
                    published_at = None
            events.append(
                {
                    "title": title,
                    "publisher": publisher,
                    "link": link,
                    "published_at": published_at,
                    "source_weight": _source_weight(publisher),
                    "sentiment": _headline_sentiment(title),
                }
            )
        return events
    except Exception:  # noqa: BLE001
        return []


def _yfinance_news_events(symbol: str) -> list[dict]:
    try:
        news_items = yf.Ticker(symbol).news or []
    except Exception:  # noqa: BLE001
        return []
    events: list[dict] = []
    for item in news_items:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        publisher = str(item.get("publisher") or "").strip()
        if not title:
            continue
        published_at = None
        publish_ts = item.get("providerPublishTime")
        if publish_ts:
            try:
                published_at = datetime.fromtimestamp(float(publish_ts), tz=timezone.utc)
            except Exception:  # noqa: BLE001
                published_at = None
        events.append(
            {
                "title": title,
                "publisher": publisher,
                "link": str(item.get("link") or ""),
                "published_at": published_at,
                "source_weight": _source_weight(publisher),
                "sentiment": _headline_sentiment(title),
            }
        )
    return events


def _attach_news_features(frame: pd.DataFrame, news_events: list[dict]) -> pd.DataFrame:
    out = frame.copy()
    if out.index.tz is None:
        out.index = out.index.tz_localize("UTC")
    else:
        out.index = out.index.tz_convert("UTC")

    events = [event for event in news_events if isinstance(event.get("published_at"), datetime)]
    events.sort(key=lambda event: event["published_at"])

    sentiment_scores: list[float] = []
    count_24h: list[float] = []
    source_score_72h: list[float] = []
    momentum_24h: list[float] = []
    momentum_72h: list[float] = []

    for ts in out.index:
        w24 = [e for e in events if e["published_at"] <= ts and (ts - e["published_at"]).total_seconds() <= 24 * 3600]
        w72 = [e for e in events if e["published_at"] <= ts and (ts - e["published_at"]).total_seconds() <= 72 * 3600]
        count_24h.append(float(len(w24)))
        source_score_72h.append(round(sum(float(e["source_weight"]) for e in w72) / max(1, len(w72)), 4) if w72 else 0.0)
        momentum_24h.append(round(sum(float(e["sentiment"]) for e in w24) / max(1, len(w24)), 4) if w24 else 0.0)
        momentum_72h.append(round(sum(float(e["sentiment"]) for e in w72) / max(1, len(w72)), 4) if w72 else 0.0)
        if w72:
            weighted_sent = sum(float(e["sentiment"]) * float(e["source_weight"]) for e in w72) / max(
                1e-9, sum(float(e["source_weight"]) for e in w72)
            )
            sentiment_scores.append(round(weighted_sent, 4))
        else:
            sentiment_scores.append(0.0)

    out["news_sentiment_score"] = sentiment_scores
    out["news_headline_count_24h"] = count_24h
    out["news_source_score_72h"] = source_score_72h
    out["news_momentum_24h"] = momentum_24h
    out["news_momentum_72h"] = momentum_72h
    return out


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
        news_events = _yfinance_news_events(symbol)
        if len(news_events) < 5:
            news_events.extend(_google_news_events(symbol))
        frame = _attach_news_features(frame, news_events)
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
