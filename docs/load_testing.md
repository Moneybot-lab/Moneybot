# Simulated API load testing

Moneybot's first launch-readiness load test is configured in `scripts/run_simulated_load_test.py`. The default scenario simulates **200 concurrent virtual users** making API requests against the running web app.

## Run the first simulated load test

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
