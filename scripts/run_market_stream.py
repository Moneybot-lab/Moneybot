from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import requests
from websockets.asyncio.client import connect

from moneybot.services.market_data_providers import MassiveRestClient
from moneybot.services.market_stream import MassiveWebSocketWorker, create_stream_state, worker_config_from_env


def main() -> int:
    config = worker_config_from_env(os.environ)
    redis_url = os.environ.get("REDIS_URL")
    api_key = (os.environ.get("MASSIVE_API_KEY") or os.environ.get("POLYGON_API_KEY") or "").strip()
    if not config.enabled:
        logging.info("Massive stream worker is disabled; set MASSIVE_STREAM_ENABLED=true to run it.")
        return 0
    if not redis_url:
        raise RuntimeError("REDIS_URL is required when MASSIVE_STREAM_ENABLED=true")
    if not api_key:
        raise RuntimeError("MASSIVE_API_KEY is required when MASSIVE_STREAM_ENABLED=true")

    state = create_stream_state(redis_url)
    rest_client = MassiveRestClient(
        api_key=api_key,
        key_source="MASSIVE_API_KEY" if os.environ.get("MASSIVE_API_KEY") else "POLYGON_API_KEY",
        timeout_seconds=float(os.environ.get("MASSIVE_TIMEOUT_SECONDS", "6")),
        retries=int(os.environ.get("MASSIVE_RETRIES", "2")),
        http_get=requests.get,
    )

    from moneybot.app_factory import create_app
    from moneybot.models import NotificationTriggerPreference, WatchlistItem

    app = create_app()

    def database_demand():
        with app.app_context():
            portfolio = {str(symbol).upper() for (symbol,) in WatchlistItem.query.with_entities(WatchlistItem.symbol).all() if symbol}
            clearview: set[str] = set()
            for (csv_value,) in NotificationTriggerPreference.query.with_entities(NotificationTriggerPreference.clearview_symbols_csv).all():
                clearview.update(part.strip().upper() for part in str(csv_value or "").split(",") if part.strip())
            return {"database:portfolio": portfolio, "database:clearview": clearview}

    worker = MassiveWebSocketWorker(
        api_key=api_key,
        state=state,
        rest_client=rest_client,
        config=config,
        connect_factory=connect,
        demand_loader=database_demand,
    )
    asyncio.run(worker.run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
