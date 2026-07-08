import os

from moneybot.app_factory import create_app
from moneybot.extensions import db
from moneybot.models import NotificationTriggerPreference


def _client(*, daily_ops_token: str | None = None):
    os.environ['MONEYBOT_SECRET_KEY'] = 'test-secret'
    os.environ['DATABASE_URL'] = 'sqlite:///:memory:'
    if daily_ops_token is None:
        os.environ.pop('DAILY_OPS_TOKEN', None)
    else:
        os.environ['DAILY_OPS_TOKEN'] = daily_ops_token
    app = create_app()
    return app.test_client()


def _signup(client, email='push@example.com', username='push_user'):
    return client.post(
        '/api/auth/signup',
        json={
            'name': 'Push User',
            'username': username,
            'email': email,
            'password': 'pw',
            'password_confirmation': 'pw',
        },
    )


def test_register_fcm_token_requires_authentication():
    client = _client()

    response = client.post('/api/notifications/fcm-token', json={'token': 'x' * 40})

    assert response.status_code == 401


def test_register_and_list_and_delete_fcm_token_flow():
    client = _client()
    signup = _signup(client)
    assert signup.status_code == 201

    token = 'fcm_token_' + ('a' * 48)
    register = client.post('/api/notifications/fcm-token', json={'token': token})
    assert register.status_code == 201
    payload = register.get_json()
    assert payload['item']['token'] == token

    listed = client.get('/api/notifications/fcm-tokens')
    assert listed.status_code == 200
    items = listed.get_json()['items']
    assert len(items) == 1
    assert items[0]['token'] == token

    deleted = client.delete('/api/notifications/fcm-token', json={'token': token})
    assert deleted.status_code == 200
    assert deleted.get_json()['removed'] is True

    listed_after = client.get('/api/notifications/fcm-tokens')
    assert listed_after.status_code == 200
    assert listed_after.get_json()['items'] == []


def test_notification_triggers_default_and_update_flow():
    client = _client()
    signup = _signup(client, email='triggers@example.com', username='trigger_user')
    assert signup.status_code == 201

    defaults_res = client.get('/api/notifications/triggers')
    assert defaults_res.status_code == 200
    defaults = defaults_res.get_json()['item']
    assert defaults['portfolio_sell_advice_change'] is True
    assert defaults['portfolio_buy_advice_change'] is True
    assert defaults['hot_momentum_score_crosses_8'] is True
    assert defaults['fresh_breakouts'] is True
    assert defaults['whale_top_investor_added'] is True

    update_res = client.put(
        '/api/notifications/triggers',
        json={
            'portfolio_sell_advice_change': False,
            'portfolio_buy_advice_change': True,
            'hot_momentum_score_crosses_8': False,
            'fresh_breakouts': False,
            'whale_top_investor_added': False,
        },
    )
    assert update_res.status_code == 200
    updated = update_res.get_json()['item']
    assert updated['portfolio_sell_advice_change'] is False
    assert updated['portfolio_buy_advice_change'] is True
    assert updated['hot_momentum_score_crosses_8'] is False
    assert updated['fresh_breakouts'] is False
    assert updated['whale_top_investor_added'] is False




def test_run_notification_triggers_sends_fresh_breakout_push(monkeypatch, tmp_path):
    client = _client(daily_ops_token='cron-secret')
    signup = _signup(client, email='breakout@example.com', username='breakout_user')
    assert signup.status_code == 201
    assert client.put('/api/notifications/triggers', json={'push_notifications_enabled': True}).status_code == 200
    assert client.post('/api/notifications/fcm-token', json={'token': 'fcm_token_' + ('r' * 48)}).status_code == 201

    app = client.application

    class _BreakoutSvc:
        def get_hot_momentum_buys(self):
            return []

        def get_breakout_radar(self):
            return [{'symbol': 'ASTC', 'score': 9.3, 'rationale': 'Confirmed intraday breakout.'}]

        def get_wells_picks(self):
            return []

    sent = []
    monkeypatch.setitem(app.extensions, 'market_data_service', _BreakoutSvc())
    monkeypatch.setattr('moneybot.api._notification_trigger_state_path', lambda: str(tmp_path / 'notification-state.json'))
    monkeypatch.setattr('moneybot.api._send_firebase_push_to_token', lambda **kwargs: sent.append(kwargs) or 'ok')
    monkeypatch.setattr('moneybot.api._is_regular_market_hours', lambda: True)

    first = client.post('/api/run-notification-triggers', headers={'X-Daily-Ops-Token': 'cron-secret'})
    assert first.status_code == 200
    assert any(msg['data']['kind'] == 'fresh_breakout' and msg['data']['symbol'] == 'ASTC' for msg in sent)

    sent.clear()
    second = client.post('/api/run-notification-triggers', headers={'X-Daily-Ops-Token': 'cron-secret'})
    assert second.status_code == 200
    assert sent == []


def test_run_notification_triggers_respects_fresh_breakout_preference(monkeypatch, tmp_path):
    client = _client(daily_ops_token='cron-secret')
    signup = _signup(client, email='breakout-off@example.com', username='breakout_off')
    assert signup.status_code == 201
    assert client.put('/api/notifications/triggers', json={'push_notifications_enabled': True, 'fresh_breakouts': False}).status_code == 200
    assert client.post('/api/notifications/fcm-token', json={'token': 'fcm_token_' + ('s' * 48)}).status_code == 201

    app = client.application

    class _BreakoutSvc:
        def get_hot_momentum_buys(self):
            return []

        def get_breakout_radar(self):
            return [{'symbol': 'BOLT', 'score': 8.8}]

        def get_wells_picks(self):
            return []

    sent = []
    monkeypatch.setitem(app.extensions, 'market_data_service', _BreakoutSvc())
    monkeypatch.setattr('moneybot.api._notification_trigger_state_path', lambda: str(tmp_path / 'notification-state.json'))
    monkeypatch.setattr('moneybot.api._send_firebase_push_to_token', lambda **kwargs: sent.append(kwargs) or 'ok')
    monkeypatch.setattr('moneybot.api._is_regular_market_hours', lambda: True)

    response = client.post('/api/run-notification-triggers', headers={'X-Daily-Ops-Token': 'cron-secret'})
    assert response.status_code == 200
    assert sent == []


def test_notification_triggers_reject_non_boolean_updates():
    client = _client()
    signup = _signup(client, email='trigger-bad@example.com', username='trigger_bad')
    assert signup.status_code == 201

    res = client.put('/api/notifications/triggers', json={'portfolio_buy_advice_change': 'yes'})
    assert res.status_code == 400
    assert 'must be a boolean' in res.get_json()['error']


def test_run_notification_triggers_requires_token():
    client = _client(daily_ops_token='cron-secret')

    res = client.post('/api/run-notification-triggers')

    assert res.status_code == 401


def test_run_notification_triggers_returns_success_with_token():
    client = _client(daily_ops_token='cron-secret')

    res = client.post('/api/run-notification-triggers', headers={'X-Daily-Ops-Token': 'cron-secret'})

    assert res.status_code == 200
    payload = res.get_json()
    assert payload['data']['success'] is True
    assert payload['data']['sent_count'] == 0


def test_run_notification_triggers_sends_clearview_hold_off_to_buy_push(monkeypatch):
    client = _client(daily_ops_token='cron-secret')
    signup = _signup(client, email='cv@example.com', username='cv_user')
    assert signup.status_code == 201

    prefs_update = client.put('/api/notifications/triggers', json={'push_notifications_enabled': True})
    assert prefs_update.status_code == 200
    token_res = client.post('/api/notifications/fcm-token', json={'token': 'fcm_token_' + ('b' * 48)})
    assert token_res.status_code == 201

    client.put('/api/clearview-symbols', json={'symbols': ['NVDA']})
    profile = client.put('/api/me/investor-profile', json={
        'profile_version': 1, 'primary_goal': 'growth', 'time_horizon_years': 10,
        'risk_tolerance': 'aggressive', 'loss_capacity_percent': 50, 'liquidity_need': 'low',
        'experience_level': 'advanced', 'account_type': 'taxable',
        'position_size_limit_percent': 80, 'sector_limit_percent': 90,
        'penny_stocks_allowed': True, 'after_hours_alerts': True,
        'recommendation_style': 'opportunity_seeking',
    })
    assert profile.status_code == 200

    app = client.application
    with app.app_context():
        pref = NotificationTriggerPreference.query.first()
        pref.clearview_symbols_csv = 'NVDA'
        db.session.commit()

    class _SignalSvc:
        def get_signal(self, symbol):
            return {'action': 'HOLD', 'score': 2.0}

        def get_hot_momentum_buys(self):
            return []

        def get_wells_picks(self):
            return []

    sent = []
    monkeypatch.setitem(app.extensions, 'market_data_service', _SignalSvc())
    monkeypatch.setitem(app.extensions, 'deterministic_quick_advisor', None)
    monkeypatch.setattr('moneybot.api._send_firebase_push_to_token', lambda **kwargs: sent.append(kwargs) or 'ok')
    monkeypatch.setattr('moneybot.api._is_regular_market_hours', lambda: True)

    first = client.post('/api/run-notification-triggers', headers={'X-Daily-Ops-Token': 'cron-secret'})
    assert first.status_code == 200
    assert sent == []

    class _LowScoreBuySvc(_SignalSvc):
        def get_signal(self, symbol):
            return {'action': 'BUY', 'score': 4.0}

    monkeypatch.setitem(app.extensions, 'market_data_service', _LowScoreBuySvc())
    low_score = client.post('/api/run-notification-triggers', headers={'X-Daily-Ops-Token': 'cron-secret'})
    assert low_score.status_code == 200
    assert sent == []

    class _BuySvc(_SignalSvc):
        def get_signal(self, symbol):
            return {'action': 'BUY', 'score': 7.0}

    monkeypatch.setitem(app.extensions, 'market_data_service', _BuySvc())
    second = client.post('/api/run-notification-triggers', headers={'X-Daily-Ops-Token': 'cron-secret'})
    assert second.status_code == 200
    assert any(msg['data']['kind'] == 'clearview_hold_off_to_buy' for msg in sent)


def test_run_notification_triggers_suppresses_after_hours_for_profile(monkeypatch, tmp_path):
    client = _client(daily_ops_token='cron-secret')
    assert _signup(client, email='quiet@example.com', username='quiet_user').status_code == 201
    assert client.put('/api/notifications/triggers', json={'push_notifications_enabled': True}).status_code == 200
    assert client.post('/api/notifications/fcm-token', json={'token': 'fcm_token_' + ('q' * 48)}).status_code == 201
    assert client.post('/api/user-watchlist', json={'symbol': 'AAPL', 'buy_price': 100, 'shares': 1}).status_code == 201
    assert client.put('/api/me/investor-profile', json={
        'profile_version': 1, 'primary_goal': 'growth', 'time_horizon_years': 10,
        'risk_tolerance': 'aggressive', 'loss_capacity_percent': 50, 'liquidity_need': 'low',
        'experience_level': 'advanced', 'account_type': 'taxable',
        'position_size_limit_percent': 80, 'sector_limit_percent': 90,
        'penny_stocks_allowed': True, 'after_hours_alerts': False,
        'recommendation_style': 'opportunity_seeking',
    }).status_code == 200

    class _Svc:
        def get_signal(self, symbol): return {'action': 'BUY', 'score': 8.0}
        def get_hot_momentum_buys(self): return []
        def get_wells_picks(self): return []

    sent = []
    monkeypatch.setitem(client.application.extensions, 'market_data_service', _Svc())
    monkeypatch.setattr('moneybot.api._notification_trigger_state_path', lambda: str(tmp_path / 'notification-state.json'))
    monkeypatch.setattr('moneybot.api._is_regular_market_hours', lambda: False)
    monkeypatch.setattr('moneybot.api._send_firebase_push_to_token', lambda **kwargs: sent.append(kwargs) or 'ok')

    response = client.post('/api/run-notification-triggers', headers={'X-Daily-Ops-Token': 'cron-secret'})

    assert response.status_code == 200
    assert response.get_json()['data']['suppressed_after_hours'] >= 1
    assert sent == []


def test_portfolio_notification_uses_same_deterministic_hold_as_portfolio_page(monkeypatch, tmp_path):
    client = _client(daily_ops_token='cron-secret')
    assert _signup(client, email='portfolio-hold@example.com', username='portfolio_hold').status_code == 201
    assert client.put('/api/notifications/triggers', json={'push_notifications_enabled': True}).status_code == 200
    assert client.post('/api/notifications/fcm-token', json={'token': 'fcm_token_' + ('h' * 48)}).status_code == 201
    assert client.post('/api/user-watchlist', json={'symbol': 'AAPL', 'buy_price': 100, 'shares': 1}).status_code == 201
    assert client.put('/api/me/investor-profile', json={
        'profile_version': 1, 'primary_goal': 'growth', 'time_horizon_years': 10,
        'risk_tolerance': 'aggressive', 'loss_capacity_percent': 50, 'liquidity_need': 'low',
        'experience_level': 'advanced', 'account_type': 'taxable',
        'position_size_limit_percent': 80, 'sector_limit_percent': 90,
        'penny_stocks_allowed': True, 'after_hours_alerts': True,
        'recommendation_style': 'opportunity_seeking',
    }).status_code == 200

    class _Svc:
        def get_signal(self, symbol):
            return {'action': 'BUY', 'score': 8.0, 'quote': self.get_quote(symbol)}
        def get_quote(self, symbol):
            return {'symbol': symbol, 'price': 101.0, 'change_percent': 1.0}
        def get_hot_momentum_buys(self): return []
        def get_wells_picks(self): return []

    class _Deterministic:
        def predict_portfolio_position(self, *, symbol, entry_price, current_price, shares, signal_data, quote_data):
            return {'advice': 'HOLD', 'probability_up': 0.45, 'confidence': 45.0}

    sent = []
    monkeypatch.setitem(client.application.extensions, 'market_data_service', _Svc())
    monkeypatch.setitem(client.application.extensions, 'deterministic_quick_advisor', _Deterministic())
    monkeypatch.setattr('moneybot.api._notification_trigger_state_path', lambda: str(tmp_path / 'notification-state.json'))
    monkeypatch.setattr('moneybot.api._is_regular_market_hours', lambda: True)
    monkeypatch.setattr('moneybot.api._send_firebase_push_to_token', lambda **kwargs: sent.append(kwargs) or 'ok')

    response = client.post('/api/run-notification-triggers', headers={'X-Daily-Ops-Token': 'cron-secret'})

    assert response.status_code == 200
    assert response.get_json()['data']['events_queued'] == 0
    assert sent == []
