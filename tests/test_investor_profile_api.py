import os

from moneybot.app_factory import create_app


def _client():
    os.environ["MONEYBOT_SECRET_KEY"] = "test-secret"
    os.environ["DATABASE_URL"] = "sqlite:///:memory:"
    return create_app().test_client()


def _signup(client, *, email="profile@example.com", username="profile_user"):
    response = client.post(
        "/api/auth/signup",
        json={
            "name": "Profile User",
            "username": username,
            "email": email,
            "password": "pw123",
            "password_confirmation": "pw123",
        },
    )
    assert response.status_code == 201


def _complete_profile_payload(version=1):
    return {
        "profile_version": version,
        "primary_goal": "growth",
        "time_horizon_years": 10,
        "risk_tolerance": "moderate",
        "loss_capacity_percent": 25,
        "liquidity_need": "low",
        "experience_level": "intermediate",
        "account_type": "roth ira",
        "position_size_limit_percent": 12.5,
        "sector_limit_percent": 30,
        "excluded_sectors": ["Tobacco", "  Gambling  ", "tobacco"],
        "penny_stocks_allowed": False,
        "after_hours_alerts": True,
        "recommendation_style": "balanced",
        "change_reason": "Completed onboarding questionnaire",
    }


def test_investor_profile_requires_authentication():
    client = _client()

    get_response = client.get("/api/me/investor-profile")
    update_response = client.put("/api/me/investor-profile", json={"profile_version": 1, "primary_goal": "growth"})
    revisions_response = client.get("/api/me/investor-profile/revisions")

    assert get_response.status_code == 401
    assert update_response.status_code == 401
    assert revisions_response.status_code == 401


def test_get_investor_profile_creates_incomplete_profile_with_safe_effective_defaults():
    client = _client()
    _signup(client)

    response = client.get("/api/me/investor-profile")

    assert response.status_code == 200
    profile = response.get_json()["profile"]
    assert profile["profile_version"] == 1
    assert profile["profile_complete"] is False
    assert profile["primary_goal"] is None
    assert "risk_tolerance" in profile["missing_fields"]
    assert profile["effective_profile"]["risk_tolerance"] == "conservative"
    assert profile["effective_profile"]["position_size_limit_percent"] == 5.0
    assert profile["effective_profile"]["penny_stocks_allowed"] is False


def test_update_investor_profile_normalizes_values_completes_profile_and_records_revision():
    client = _client()
    _signup(client)
    client.get("/api/me/investor-profile")

    response = client.put("/api/me/investor-profile", json=_complete_profile_payload())

    assert response.status_code == 200
    profile = response.get_json()["profile"]
    assert profile["profile_version"] == 2
    assert profile["profile_complete"] is True
    assert profile["missing_fields"] == []
    assert profile["account_type"] == "roth_ira"
    assert profile["excluded_sectors"] == ["tobacco", "gambling"]
    assert profile["position_size_limit_percent"] == 12.5
    assert profile["questionnaire_completed_at"] is not None

    revisions_response = client.get("/api/me/investor-profile/revisions")
    assert revisions_response.status_code == 200
    revisions = revisions_response.get_json()["items"]
    assert len(revisions) == 1
    assert revisions[0]["profile_version"] == 2
    assert revisions[0]["previous_profile"]["primary_goal"] is None
    assert revisions[0]["new_profile"]["primary_goal"] == "growth"
    assert revisions[0]["change_reason"] == "Completed onboarding questionnaire"
    assert revisions[0]["source"] == "settings"


def test_update_investor_profile_rejects_invalid_and_unknown_fields():
    client = _client()
    _signup(client)

    response = client.put(
        "/api/me/investor-profile",
        json={
            "profile_version": 1,
            "risk_tolerance": "maximum",
            "time_horizon_years": 0,
            "penny_stocks_allowed": "yes",
            "secret_score": 99,
        },
    )

    assert response.status_code == 400
    data = response.get_json()
    assert data["error"] == "invalid investor profile"
    assert set(data["fields"]) == {
        "penny_stocks_allowed",
        "risk_tolerance",
        "time_horizon_years",
        "unknown_fields",
    }


def test_update_investor_profile_requires_current_version_and_returns_latest_profile():
    client = _client()
    _signup(client)
    first = client.put(
        "/api/me/investor-profile",
        json={"profile_version": 1, "primary_goal": "income"},
    )
    assert first.status_code == 200

    stale = client.put(
        "/api/me/investor-profile",
        json={"profile_version": 1, "primary_goal": "growth"},
    )

    assert stale.status_code == 409
    data = stale.get_json()
    assert data["error"] == "investor profile version conflict"
    assert data["current_profile"]["profile_version"] == 2
    assert data["current_profile"]["primary_goal"] == "income"


def test_no_op_profile_update_does_not_increment_version_or_add_revision():
    client = _client()
    _signup(client)
    first = client.put(
        "/api/me/investor-profile",
        json={"profile_version": 1, "primary_goal": "income"},
    )
    assert first.status_code == 200

    no_op = client.put(
        "/api/me/investor-profile",
        json={"profile_version": 2, "primary_goal": "income"},
    )

    assert no_op.status_code == 200
    assert no_op.get_json()["profile"]["profile_version"] == 2
    revisions = client.get("/api/me/investor-profile/revisions").get_json()["items"]
    assert len(revisions) == 1


def test_profile_revisions_are_isolated_to_authenticated_user():
    first_client = _client()
    _signup(first_client, email="first@example.com", username="first_user")
    update = first_client.put(
        "/api/me/investor-profile",
        json={"profile_version": 1, "primary_goal": "growth"},
    )
    assert update.status_code == 200

    second_client = _client()
    _signup(second_client, email="second@example.com", username="second_user")

    response = second_client.get("/api/me/investor-profile/revisions")

    assert response.status_code == 200
    assert response.get_json()["items"] == []
