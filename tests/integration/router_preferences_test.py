# pylint: disable=missing-function-docstring
"""Display preferences (#122)."""

from fastapi.testclient import TestClient


def _auth(token: str) -> dict:
    return {'Authorization': f"Bearer {token}"}


def test_default_is_25(test_client: TestClient):
    body = test_client.get(
        '/v1/users/me/preferences', headers=_auth(test_client.first_user.token)
    ).json()
    assert body == {'ranked_list_length': '25', 'onboarding_completed': False}


def test_set_and_read_back(test_client: TestClient):
    token = test_client.first_user.token
    updated = test_client.put(
        '/v1/users/me/preferences',
        headers=_auth(token),
        json={'ranked_list_length': 'all'},
    )
    assert updated.status_code == 200
    assert updated.json() == {
        'ranked_list_length': 'all',
        'onboarding_completed': False,
    }

    fetched = test_client.get('/v1/users/me/preferences', headers=_auth(token))
    assert fetched.json() == {
        'ranked_list_length': 'all',
        'onboarding_completed': False,
    }


def test_set_onboarding_completed(test_client: TestClient):
    token = test_client.first_user.token
    updated = test_client.put(
        '/v1/users/me/preferences',
        headers=_auth(token),
        json={'onboarding_completed': True},
    )
    assert updated.status_code == 200
    assert updated.json() == {'ranked_list_length': '25', 'onboarding_completed': True}

    fetched = test_client.get('/v1/users/me/preferences', headers=_auth(token))
    assert fetched.json() == {'ranked_list_length': '25', 'onboarding_completed': True}


def test_invalid_length_rejected(test_client: TestClient):
    response = test_client.put(
        '/v1/users/me/preferences',
        headers=_auth(test_client.first_user.token),
        json={'ranked_list_length': '37'},
    )
    assert response.status_code == 422


def test_preferences_are_per_user(test_client: TestClient):
    test_client.put(
        '/v1/users/me/preferences',
        headers=_auth(test_client.first_user.token),
        json={'ranked_list_length': '100'},
    )
    other = test_client.get(
        '/v1/users/me/preferences', headers=_auth(test_client.second_user.token)
    ).json()
    assert other == {'ranked_list_length': '25', 'onboarding_completed': False}


def test_preferences_require_auth(test_client: TestClient):
    assert test_client.get('/v1/users/me/preferences').status_code == 401
    assert (
        test_client.put(
            '/v1/users/me/preferences', json={'ranked_list_length': '50'}
        ).status_code
        == 401
    )
