from moneybot.services.suitability_policy import UserDecisionContext, apply_suitability_policy


def _context(**overrides):
    values = {
        "profile_version": 4,
        "profile_complete": True,
        "primary_goal": "growth",
        "time_horizon_years": 10,
        "risk_tolerance": "moderate",
        "loss_capacity_percent": 25.0,
        "liquidity_need": "low",
        "experience_level": "intermediate",
        "account_type": "roth_ira",
        "position_size_limit_percent": 15.0,
        "sector_limit_percent": 35.0,
        "excluded_sectors": (),
        "penny_stocks_allowed": False,
        "after_hours_alerts": False,
        "recommendation_style": "balanced",
    }
    values.update(overrides)
    return UserDecisionContext(**values)


def test_policy_never_creates_a_buy_or_sell_from_hold():
    result = apply_suitability_policy(base_action="HOLD", context=_context(), symbol="AAPL")

    assert result.action == "HOLD"
    assert result.changed is False
    assert result.applied_rules == ()


def test_policy_blocks_penny_stock_buy_when_profile_disallows_it():
    result = apply_suitability_policy(
        base_action="BUY",
        context=_context(penny_stocks_allowed=False),
        symbol="PENNY",
        current_price=2.75,
        probability_up=0.91,
        position_weight_percent=1.0,
    )

    assert result.base_action == "BUY"
    assert result.action == "HOLD"
    assert result.changed is True
    assert [rule["code"] for rule in result.applied_rules] == ["penny_stocks_disabled"]


def test_policy_applies_position_and_sector_limits_with_traceable_details():
    result = apply_suitability_policy(
        base_action="BUY",
        context=_context(position_size_limit_percent=10, sector_limit_percent=25),
        symbol="AAPL",
        current_price=200,
        probability_up=0.85,
        position_weight_percent=12.5,
        sector="Technology",
        sector_weight_percent=30,
    )

    assert result.action == "HOLD"
    assert [rule["code"] for rule in result.applied_rules] == [
        "position_limit_reached",
        "sector_limit_reached",
    ]
    assert result.applied_rules[0]["details"]["limit_percent"] == 10


def test_conservative_profile_requires_higher_buy_confidence():
    result = apply_suitability_policy(
        base_action="BUY",
        context=_context(risk_tolerance="conservative", recommendation_style="conservative"),
        symbol="MSFT",
        current_price=400,
        probability_up=0.66,
        position_weight_percent=4,
    )

    assert result.action == "HOLD"
    rule = result.applied_rules[0]
    assert rule["code"] == "confidence_below_profile_threshold"
    assert rule["details"]["required_confidence"] == 0.70


def test_high_liquidity_short_horizon_requires_strongest_confidence():
    result = apply_suitability_policy(
        base_action="BUY",
        context=_context(liquidity_need="high", time_horizon_years=2),
        symbol="MSFT",
        current_price=400,
        probability_up=0.72,
        position_weight_percent=4,
    )

    assert result.action == "HOLD"
    assert result.applied_rules[0]["details"]["required_confidence"] == 0.75


def test_aggressive_profile_preserves_buy_when_no_guardrail_is_breached():
    result = apply_suitability_policy(
        base_action="BUY",
        context=_context(
            risk_tolerance="aggressive",
            recommendation_style="opportunity_seeking",
            penny_stocks_allowed=True,
        ),
        symbol="AAPL",
        current_price=200,
        probability_up=0.55,
        position_weight_percent=8,
    )

    assert result.action == "BUY"
    assert result.changed is False
    assert result.profile_version == 4


def test_preservation_goal_and_low_loss_capacity_raise_buy_threshold():
    result = apply_suitability_policy(
        base_action="BUY",
        context=_context(primary_goal="preservation", loss_capacity_percent=8),
        symbol="JNJ",
        current_price=150,
        probability_up=0.73,
        position_weight_percent=4,
    )

    assert result.action == "HOLD"
    details = result.applied_rules[0]["details"]
    assert details["required_confidence"] == 0.75
    assert "capital-preservation goal" in details["reasons"]
    assert "low loss capacity" in details["reasons"]


def test_runtime_modes_and_rollout_preserve_shared_contract():
    from moneybot.services.suitability_policy import PersonalizationRuntime

    context = _context(risk_tolerance="conservative")
    off = PersonalizationRuntime(mode="off")
    shadow = PersonalizationRuntime(mode="shadow", allowlist={7})
    enforce = PersonalizationRuntime(mode="enforce", allowlist={7})

    off_decision = off.evaluate(user_id=7, context=context, endpoint="quick_ask", symbol="AAPL", base_action="BUY", forecast_horizon="short_term", probability_up=0.5)
    shadow_decision = shadow.evaluate(user_id=7, context=context, endpoint="quick_ask", symbol="AAPL", base_action="BUY", forecast_horizon="short_term", probability_up=0.5)
    enforce_decision = enforce.evaluate(user_id=7, context=context, endpoint="quick_ask", symbol="AAPL", base_action="BUY", forecast_horizon="short_term", probability_up=0.5)

    assert off_decision.payload()["action"] == "BUY"
    assert shadow_decision.payload()["action"] == "BUY"
    assert shadow_decision.payload()["policy_action"] == "HOLD"
    assert shadow_decision.payload()["would_change"] is True
    assert enforce_decision.payload()["action"] == "HOLD"
    assert enforce_decision.payload()["policy_schema_version"] == "suitability.v1"

    disabled = PersonalizationRuntime(profile_enabled=False, policy_enabled=False, mode="enforce")
    disabled_decision = disabled.evaluate(user_id=7, context=context, endpoint="quick_ask", symbol="AAPL", base_action="BUY", forecast_horizon="short_term", probability_up=0.5)
    assert disabled_decision.payload()["action"] == "BUY"
    assert disabled_decision.payload()["policy_action"] == "BUY"
    assert disabled_decision.payload()["applied_rules"] == []


def test_unknown_sector_and_missing_quote_do_not_claim_false_compliance():
    result = apply_suitability_policy(
        base_action="BUY",
        context=_context(
            risk_tolerance="aggressive",
            recommendation_style="opportunity_seeking",
            penny_stocks_allowed=True,
        ),
        symbol="UNKNOWN",
        current_price=None,
        probability_up=0.80,
        position_weight_percent=0,
        sector=None,
        sector_weight_percent=None,
    )

    assert result.action == "BUY"
    assert result.applied_rules == ()
    assert all(rule["code"] != "sector_limit_reached" for rule in result.applied_rules)


def test_zero_value_position_is_not_treated_as_concentrated():
    result = apply_suitability_policy(
        base_action="BUY",
        context=_context(
            position_size_limit_percent=10,
            risk_tolerance="aggressive",
            recommendation_style="opportunity_seeking",
            penny_stocks_allowed=True,
        ),
        symbol="CASHLESS",
        current_price=100,
        probability_up=0.80,
        position_weight_percent=0,
    )

    assert result.action == "BUY"
    assert "position_limit_reached" not in [rule["code"] for rule in result.applied_rules]
