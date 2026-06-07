import os

from moneybot.app_factory import create_app


def _client():
    os.environ["MONEYBOT_SECRET_KEY"] = "test-secret"
    os.environ["DATABASE_URL"] = "sqlite:///:memory:"
    return create_app().test_client()


def test_settings_page_renders_investor_profile_questionnaire():
    response = _client().get("/settings")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Advice that fits the investor—not just the ticker." in html
    assert 'id="investorProfileForm"' in html
    assert 'id="primaryGoal"' in html
    assert 'id="riskTolerance"' in html
    assert 'id="lossCapacityPercent"' in html
    assert 'id="positionSizeLimitPercent"' in html
    assert 'id="pennyStocksAllowed"' in html
    assert "Missing answers use conservative effective defaults" in html
    assert '/static/js/settings.js' in html


def test_settings_javascript_uses_versioned_profile_api_and_handles_conflicts():
    response = _client().get("/static/js/settings.js")

    assert response.status_code == 200
    javascript = response.get_data(as_text=True)
    assert "apiFetch('/api/me/investor-profile')" in javascript
    assert "profile_version: originalInvestorProfile.profile_version" in javascript
    assert "response.status === 409" in javascript
    assert "Latest values loaded" in javascript
    assert "change_reason: 'Updated investor profile from Account Settings'" in javascript
