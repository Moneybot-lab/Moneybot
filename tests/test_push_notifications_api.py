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
