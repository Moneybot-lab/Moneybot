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
  --database-setup-mode setup-first \
  --database-setup-concurrency 20 \
  --database-timeout-seconds 30 \
  --ramp-up-seconds 15 \
  --max-throttle-rate 0.05 \
  --max-p95-ms 5000 \
  --rate-limit-token "$MONEYBOT_LOAD_TEST_RATE_LIMIT_TOKEN" \
  --output data/render_load_test_200_vu_report.json
```

This exercises:

- **Response time**: captured in the JSON report as `latency_ms.min`, `latency_ms.avg`, `latency_ms.p95`, `latency_ms.max`, and per-endpoint `p95_ms`.
- **Errors and throttling**: captured as `failures`, `failure_rate`, `throttled`, `throttle_rate`, per-endpoint failures, per-endpoint `status_counts`, and `sample_failures`.
- **Database**: with `--include-database-flow --database-setup-mode setup-first`, users are signed up, logged in, and seeded with a watchlist row before the measured window starts; the measured phase then includes authenticated database reads (`/api/user-watchlist?skip_market_data=1` and `/api/portfolio-summary?skip_market_data=1`) alongside the public API mix. Use `--database-setup-mode inline` only when you intentionally want signup/login/write latency included in the measured p95.
- **Render CPU and RAM**: inspect the same test window in the Render service Metrics page. Render exposes CPU and memory usage in the dashboard's Application Metrics section; use the report's `test_window_utc` and `duration_seconds` to line up the graph window.
- **Render database activity**: inspect the Render Postgres Metrics page for active connections, disk, and database activity over the same window.

> Warning: `--include-database-flow` creates test users and watchlist rows in the target database. Run it against staging first, or use a unique `--run-id` so records are easy to identify and clean up.

If database endpoints time out while public read-only endpoints stay fast, rerun with `--database-timeout-seconds 30` (or higher), `--database-setup-mode setup-first`, `--database-setup-concurrency 20`, and `--ramp-up-seconds 15`. This keeps expensive signup/login setup out of the measured p95 and avoids firing all database setup requests at exactly the same instant. The public API timeout remains controlled separately by `--timeout-seconds`.

If the report shows many HTTP `429` responses, the application rate limiter is protecting the service and masking true infrastructure capacity. For launch-readiness tests, either reduce generated request volume, temporarily raise `API_RATE_LIMIT_MAX_REQUESTS`, or configure `LOAD_TEST_RATE_LIMIT_TOKEN` (or `MONEYBOT_LOAD_TEST_RATE_LIMIT_TOKEN`) on the target environment and pass the matching `--rate-limit-token` value from your local shell. Before running, verify `echo "$MONEYBOT_LOAD_TEST_RATE_LIMIT_TOKEN"` prints a non-empty value; the script rejects an explicitly empty `--rate-limit-token` argument.

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
- `MONEYBOT_LOAD_TEST_DATABASE_TIMEOUT_SECONDS`
- `MONEYBOT_LOAD_TEST_DATABASE_SETUP_MODE`
- `MONEYBOT_LOAD_TEST_DATABASE_SETUP_CONCURRENCY`
- `MONEYBOT_LOAD_TEST_THINK_TIME_SECONDS`
- `MONEYBOT_LOAD_TEST_RAMP_UP_SECONDS`
- `MONEYBOT_LOAD_TEST_OUTPUT`
- `MONEYBOT_LOAD_TEST_MAX_FAILURE_RATE`
- `MONEYBOT_LOAD_TEST_MAX_THROTTLE_RATE`
- `MONEYBOT_LOAD_TEST_MAX_P95_MS`
- `MONEYBOT_LOAD_TEST_RATE_LIMIT_TOKEN`
- `MONEYBOT_LOAD_TEST_INCLUDE_DATABASE_FLOW`
- `MONEYBOT_LOAD_TEST_RUN_ID`

## Related server-side tuning

For controlled load-test environments, the API limiter can be tuned with:

- `API_RATE_LIMIT_WINDOW_SECONDS` (default: `60`)
- `API_RATE_LIMIT_MAX_REQUESTS` (default: `120`)
- `LOAD_TEST_RATE_LIMIT_TOKEN` (preferred server-side secret that allows requests with a matching `X-Load-Test-Token` header to bypass the limiter)
- `MONEYBOT_LOAD_TEST_RATE_LIMIT_TOKEN` (also accepted by the server for consistency with the local load-test runner)

Keep the load-test token secret, only enable it for planned tests, and rotate or remove it after testing. Do not raise limits globally without checking abuse-protection needs for the public site.
