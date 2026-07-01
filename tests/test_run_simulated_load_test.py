from __future__ import annotations

from scripts import run_simulated_load_test as load_test


def test_summarize_reports_200_virtual_users():
    results = [
        load_test.RequestResult(endpoint="/api/model-health", status_code=200, elapsed_ms=10.0, ok=True),
        load_test.RequestResult(endpoint="/api/quote?symbol=AAPL", status_code=503, elapsed_ms=30.0, ok=False, error="HTTP 503"),
        load_test.RequestResult(endpoint="/api/quote?symbol=AAPL", status_code=429, elapsed_ms=5.0, ok=True),
    ]

    report = load_test.summarize(results, users=200, duration_seconds=60, base_url="https://example.test")

    assert report["schema_version"] == "moneybot.load_test.v1"
    assert report["virtual_users"] == 200
    assert report["requests"] == 3
    assert report["failures"] == 1
    assert report["failure_rate"] == 0.3333
    assert report["throttled"] == 1
    assert report["throttle_rate"] == 0.3333
    assert report["by_endpoint"]["/api/model-health"]["requests"] == 1
    assert report["by_endpoint"]["/api/quote?symbol=AAPL"]["status_counts"] == {"503": 1, "429": 1}


def test_run_load_test_uses_configured_endpoint(monkeypatch):
    calls = []

    class FakeResponse:
        def __init__(self, status_code=200, text=""):
            self.status_code = status_code
            self.text = text
            self.headers = {"X-Request-ID": "req-test"}

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def request(self, method, url, timeout, json=None, headers=None):
            calls.append((method, url, timeout, json, headers))
            if url.endswith("/api/auth/signup") or url.endswith("/api/user-watchlist") and method == "POST":
                return FakeResponse(201)
            return FakeResponse(200)

    monotonic_values = iter([0.0, 0.5, 1.5])

    def fake_monotonic():
        return next(monotonic_values)

    monkeypatch.setattr(load_test.requests, "Session", FakeSession)
    monkeypatch.setattr(load_test.time, "monotonic", fake_monotonic)
    monkeypatch.setattr(load_test.time, "sleep", lambda _: None)

    report = load_test.run_load_test(
        base_url="https://moneybot.test",
        users=1,
        duration_seconds=1,
        endpoints=("/api/model-health",),
        timeout=3,
        think_time_seconds=0,
    )

    assert report["requests"] == 1
    assert calls == [("GET", "https://moneybot.test/api/model-health", 3, None, None)]


def test_database_probe_requests_are_unique_per_user():
    first = load_test._database_probe_requests(1, "rendercpu")
    second = load_test._database_probe_requests(2, "rendercpu")

    assert first[0][0] == "POST"
    assert first[0][1] == "/api/auth/signup"
    assert first[0][2]["email"] != second[0][2]["email"]
    assert [step[1] for step in first] == [
        "/api/auth/signup",
        "/api/auth/login",
        "/api/user-watchlist",
        "/api/user-watchlist?skip_market_data=1",
        "/api/portfolio-summary?skip_market_data=1",
    ]
    assert first[0][3] == {201, 409}
    assert first[1][3] == {200}
    assert first[0][4] is True


def test_database_flow_uses_database_timeout_and_ramp_up(monkeypatch):
    calls = []
    sleeps = []

    class FakeResponse:
        def __init__(self, status_code=200, text=""):
            self.status_code = status_code
            self.text = text
            self.headers = {"X-Request-ID": "req-test"}

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def request(self, method, url, timeout, json=None, headers=None):
            calls.append((method, url, timeout, json, headers))
            if url.endswith("/api/auth/signup") or (url.endswith("/api/user-watchlist") and method == "POST"):
                return FakeResponse(201)
            return FakeResponse(200)

    monotonic_values = iter([0.0, 0.5, 1.5])

    def fake_monotonic():
        return next(monotonic_values)

    monkeypatch.setattr(load_test.requests, "Session", FakeSession)
    monkeypatch.setattr(load_test.time, "monotonic", fake_monotonic)
    monkeypatch.setattr(load_test.time, "sleep", lambda delay: sleeps.append(delay))

    report = load_test.run_load_test(
        base_url="https://moneybot.test",
        users=1,
        duration_seconds=1,
        endpoints=("/api/model-health",),
        timeout=3,
        database_timeout=30,
        think_time_seconds=0,
        include_database_flow=True,
        run_id="dbtest",
        ramp_up_seconds=5,
    )

    assert report["database_flow_enabled"] is True
    assert len(calls) == 6
    assert calls[0][:3] == ("POST", "https://moneybot.test/api/auth/signup", 30)
    assert calls[3][:3] == ("GET", "https://moneybot.test/api/user-watchlist?skip_market_data=1", 30)
    assert calls[4][:3] == ("GET", "https://moneybot.test/api/portfolio-summary?skip_market_data=1", 30)
    assert calls[5][:3] == ("GET", "https://moneybot.test/api/model-health", 3)
    assert len(sleeps) == 1
    assert 0 <= sleeps[0] <= 5


def test_database_flow_stops_after_auth_setup_failure(monkeypatch):
    calls = []

    class FakeResponse:
        def __init__(self, status_code, text="server error"):
            self.status_code = status_code
            self.text = text
            self.headers = {"X-Request-ID": "req-failed"}

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def request(self, method, url, timeout, json=None, headers=None):
            calls.append((method, url, timeout, json, headers))
            return FakeResponse(500)

    monotonic_values = iter([0.0, 1.5])

    def fake_monotonic():
        return next(monotonic_values)

    monkeypatch.setattr(load_test.requests, "Session", FakeSession)
    monkeypatch.setattr(load_test.time, "monotonic", fake_monotonic)
    monkeypatch.setattr(load_test.time, "sleep", lambda _: None)

    report = load_test.run_load_test(
        base_url="https://moneybot.test",
        users=1,
        duration_seconds=1,
        endpoints=("/api/model-health",),
        database_timeout=30,
        include_database_flow=True,
        run_id="dbtest",
    )

    assert report["requests"] == 1
    assert report["failures"] == 1
    assert report["sample_failures"][0]["request_id"] == "req-failed"
    assert report["sample_failures"][0]["response_excerpt"] == "server error"
    assert calls == [("POST", "https://moneybot.test/api/auth/signup", 30, calls[0][3], None)]


def test_rate_limit_token_is_sent_as_header(monkeypatch):
    calls = []

    class FakeResponse:
        status_code = 200
        text = ""
        headers = {}

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def request(self, method, url, timeout, json=None, headers=None):
            calls.append(headers)
            return FakeResponse()

    monotonic_values = iter([0.0, 0.5, 1.5])

    def fake_monotonic():
        return next(monotonic_values)

    monkeypatch.setattr(load_test.requests, "Session", FakeSession)
    monkeypatch.setattr(load_test.time, "monotonic", fake_monotonic)
    monkeypatch.setattr(load_test.time, "sleep", lambda _: None)

    load_test.run_load_test(
        base_url="https://moneybot.test",
        users=1,
        duration_seconds=1,
        endpoints=("/api/model-health",),
        think_time_seconds=0,
        rate_limit_token="secret-load-test",
    )

    assert calls == [{"X-Load-Test-Token": "secret-load-test"}]
