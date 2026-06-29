# Simulated API load testing

Moneybot's first launch-readiness load test is configured in `scripts/run_simulated_load_test.py`. The default scenario simulates **200 concurrent virtual users** making API requests against the running web app.

## Run the Render 200-user infrastructure test

Point the test at the Render service URL and enable the database flow:

```bash
python scripts/run_simulated_load_test.py \
  --base-url https://YOUR-RENDER-SERVICE.onrender.com \
  --users 200 \
  --duration-seconds 60 \
  --include-database-flow \
  --output data/render_load_test_200_vu_report.json
```

This exercises:

- **Response time**: captured in the JSON report as `latency_ms.min`, `latency_ms.avg`, `latency_ms.p95`, and `latency_ms.max`.
- **Errors**: captured as `failures`, `failure_rate`, per-endpoint failures, and `sample_failures`.
- **Database**: each virtual user signs up, logs in, writes a watchlist row, reads the watchlist, and reads the portfolio summary when `--include-database-flow` is set.
- **Render CPU and RAM**: inspect the same test window in the Render service Metrics page. Render exposes CPU and memory usage in the dashboard's Application Metrics section; use the report's `test_window_utc` and `duration_seconds` to line up the graph window.
- **Render database activity**: inspect the Render Postgres Metrics page for active connections, disk, and database activity over the same window.

> Warning: `--include-database-flow` creates test users and watchlist rows in the target database. Run it against staging first, or use a unique `--run-id` so records are easy to identify and clean up.

## Run the local first simulated load test

Start the app in one terminal:

```bash
python app.py
```

Run the 200-user load test in another terminal:

```bash
python scripts/run_simulated_load_test.py --base-url http://127.0.0.1:5000 --users 200 --duration-seconds 60
```

The script writes a JSON report to `data/load_test_200_vu_report.json` by default and exits non-zero if the failure rate is above `--max-failure-rate` (default: `0.05`).

## Default API scenario

The default mix covers public API paths that exercise model health, market quote, signal, and quick-advice flows:

- `/api/model-health`
- `/api/quote?symbol=AAPL`
- `/api/signal?symbol=MSFT`
- `/api/quick-ask?symbol=NVDA`

Override the request mix by passing one or more `--endpoint` flags.

## Useful environment variables

- `MONEYBOT_LOAD_TEST_BASE_URL`
- `MONEYBOT_LOAD_TEST_USERS`
- `MONEYBOT_LOAD_TEST_DURATION_SECONDS`
- `MONEYBOT_LOAD_TEST_TIMEOUT_SECONDS`
- `MONEYBOT_LOAD_TEST_THINK_TIME_SECONDS`
- `MONEYBOT_LOAD_TEST_OUTPUT`
- `MONEYBOT_LOAD_TEST_MAX_FAILURE_RATE`
- `MONEYBOT_LOAD_TEST_INCLUDE_DATABASE_FLOW`
- `MONEYBOT_LOAD_TEST_RUN_ID`
