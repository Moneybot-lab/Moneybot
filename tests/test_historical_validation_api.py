import json
import os

from moneybot.app_factory import create_app


def _app(tmp_path):
    os.environ["MONEYBOT_SECRET_KEY"] = "test-secret"
    os.environ["DATABASE_URL"] = "sqlite:///:memory:"
    os.environ["DAILY_OPS_TOKEN"] = "ops-secret"
    app = create_app()
    path = tmp_path / "historical_validation_report.json"
    app.config.update(TESTING=True, HISTORICAL_VALIDATION_REPORT_PATH=str(path))
    return app, path


def test_historical_validation_endpoint_requires_ops_token(tmp_path):
    app, _path = _app(tmp_path)
    response = app.test_client().get("/api/historical-validation")
    assert response.status_code == 401


def test_historical_validation_endpoint_returns_report_and_model_health_summary(tmp_path):
    app, path = _app(tmp_path)
    payload = {
        "schema_version": "historical_validation.v1",
        "generated_at_utc": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        "computed_at_utc": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        "metrics": {"evaluated_rows": 42, "brier_score": 0.21, "avg_net_return": 0.01},
        "promotion_gates": {"promotion_ready": True, "failed_blockers": 0},
        "rollout_recommendation": "promote",
        "required_next_steps": [],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    client = app.test_client()

    response = client.get("/api/historical-validation", headers={"X-Daily-Ops-Token": "ops-secret"})
    health = client.get("/api/model-health").get_json()["data"]["historical_validation"]

    assert response.status_code == 200
    assert response.get_json()["data"]["rollout_recommendation"] == "promote"
    assert health["fresh"] is True
    assert health["promotion_ready"] is True
    assert health["evaluated_rows"] == 42


def test_historical_validation_endpoint_reports_missing_artifact(tmp_path):
    app, _path = _app(tmp_path)
    response = app.test_client().get("/api/historical-validation", headers={"X-Daily-Ops-Token": "ops-secret"})
    assert response.status_code == 404
    assert response.get_json()["status"]["rollout_recommendation"] == "hold_shadow"
