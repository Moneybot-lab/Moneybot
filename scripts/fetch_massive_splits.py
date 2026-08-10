#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from moneybot.services.corporate_actions import (
    CORPORATE_ACTION_SCHEMA_VERSION,
    SUPPORTED_ADJUSTMENT_TYPES,
    canonical_splits,
    normalize_split,
    split_source_hash,
)
from moneybot.services.decision_log import read_decision_events
from moneybot.services.outcome_tracking import normalize_unix_ts

ENDPOINT = "https://api.massive.com/stocks/v1/splits"


def event_universe(path: Path, history_days: int = 70, label_days: int = 8):
    events = read_decision_events(path)
    symbols = sorted(
        {
            str(event.get("symbol") or "").strip().upper()
            for event in events
            if event.get("symbol")
        }
    )
    days = [
        datetime.fromtimestamp(ts, tz=timezone.utc).date()
        for event in events
        if (ts := normalize_unix_ts(event.get("ts"))) is not None
    ]
    if not days:
        raise ValueError("Decision log contains no dated events for split retrieval")
    from datetime import timedelta

    return (
        symbols,
        (min(days) - timedelta(days=history_days)).isoformat(),
        (max(days) + timedelta(days=label_days)).isoformat(),
    )


def fetch_pages(
    api_key: str, start_date: str, end_date: str, *, opener=urllib.request.urlopen
):
    query = urllib.parse.urlencode(
        {
            "execution_date.gte": start_date,
            "execution_date.lte": end_date,
            "limit": 1000,
            "sort": "execution_date.asc",
            "apiKey": api_key,
        }
    )
    url = f"{ENDPOINT}?{query}"
    records, pages = [], 0
    while url:
        request = urllib.request.Request(
            url,
            headers={"Accept": "application/json", "User-Agent": "MoneyBot-Track-B/1"},
        )
        with opener(request, timeout=60) as response:
            payload = json.loads(response.read().decode("utf-8"))
        pages += 1
        records.extend(payload.get("results") or [])
        url = payload.get("next_url")
        if url and "apiKey=" not in url:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(
                {"apiKey": api_key}
            )
    return records, pages


def materialize(
    decision_log: Path, output_dir: Path, api_key: str, *, opener=urllib.request.urlopen
):
    symbols, start_date, end_date = event_universe(decision_log)
    raw, pages = fetch_pages(api_key, start_date, end_date, opener=opener)
    wanted = set(symbols) | {
        "SPY",
        "XLC",
        "XLY",
        "XLP",
        "XLE",
        "XLF",
        "XLV",
        "XLI",
        "XLB",
        "XLRE",
        "XLK",
        "XLU",
    }
    splits = [item for item in canonical_splits(raw) if item["ticker"] in wanted]
    unsupported_types = sorted(
        {
            str(item.get("adjustment_type") or "unknown")
            for item in raw
            if str(item.get("ticker") or "").strip().upper() in wanted
            and str(item.get("adjustment_type") or "").strip().lower()
            not in SUPPORTED_ADJUSTMENT_TYPES
        }
    )
    invalid_share_actions = [
        {
            "ticker": str(item.get("ticker") or "").strip().upper(),
            "execution_date": str(item.get("execution_date") or "")[:10],
            "adjustment_type": str(item.get("adjustment_type") or "unknown"),
            "id": str(item.get("id") or ""),
        }
        for item in raw
        if str(item.get("ticker") or "").strip().upper() in wanted
        and str(item.get("adjustment_type") or "").strip().lower()
        in SUPPORTED_ADJUSTMENT_TYPES
        and normalize_split(item) is None
    ]
    output_dir.mkdir(parents=True, exist_ok=True)
    cache = output_dir / "massive_splits.jsonl"
    cache.write_text(
        "".join(json.dumps(item, sort_keys=True) + "\n" for item in splits),
        encoding="utf-8",
    )
    manifest = {
        "schema_version": CORPORATE_ACTION_SCHEMA_VERSION,
        "source": "massive",
        "endpoint": "/stocks/v1/splits",
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
        "requested_start_date": start_date,
        "requested_end_date": end_date,
        "requested_symbols": symbols,
        "split_event_count": len(splits),
        "affected_symbol_count": len({item["ticker"] for item in splits}),
        "response_page_count": pages,
        "source_hash": split_source_hash(splits),
        "unsupported_adjustment_types_audited_not_applied": unsupported_types,
        "invalid_share_actions": invalid_share_actions,
        "corporate_action_normalization_passed": not unsupported_types
        and not invalid_share_actions,
    }
    (output_dir / "split_adjustment_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    if unsupported_types or invalid_share_actions:
        raise ValueError(
            "Unsupported share-count corporate actions require explicit semantics: "
            + ", ".join(unsupported_types or ["malformed split_from/split_to"])
        )
    return manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--decision-log", default="data/decision_events.jsonl")
    parser.add_argument("--output-dir", default="data/track_b/corporate_actions")
    args = parser.parse_args()
    api_key = str(os.environ.get("MASSIVE_API_KEY") or "").strip()
    if not api_key:
        raise SystemExit(
            "MASSIVE_API_KEY is required to materialize canonical split metadata"
        )
    print(
        json.dumps(
            materialize(Path(args.decision_log), Path(args.output_dir), api_key),
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
