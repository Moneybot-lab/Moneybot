import os

from moneybot.app_factory import create_app


def _client():
    os.environ['MONEYBOT_SECRET_KEY'] = 'test-secret'
    os.environ['DATABASE_URL'] = 'sqlite:///:memory:'
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
    assert defaults['whale_top_investor_added'] is True
    assert defaults['whales_top_stock_list_changes'] is True

    update_res = client.put(
        '/api/notifications/triggers',
        json={
            'portfolio_sell_advice_change': False,
            'portfolio_buy_advice_change': True,
            'hot_momentum_score_crosses_8': False,
            'whale_top_investor_added': False,
            'whales_top_stock_list_changes': False,
        },
    )
    assert update_res.status_code == 200
    updated = update_res.get_json()['item']
    assert updated['portfolio_sell_advice_change'] is False
    assert updated['portfolio_buy_advice_change'] is True
    assert updated['hot_momentum_score_crosses_8'] is False
    assert updated['whale_top_investor_added'] is False
    assert updated['whales_top_stock_list_changes'] is False


def test_notification_triggers_reject_non_boolean_updates():
    client = _client()
    signup = _signup(client, email='trigger-bad@example.com', username='trigger_bad')
    assert signup.status_code == 201

    res = client.put('/api/notifications/triggers', json={'portfolio_buy_advice_change': 'yes'})
    assert res.status_code == 400
    assert 'must be a boolean' in res.get_json()['error']
