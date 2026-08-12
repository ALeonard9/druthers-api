# pylint: disable=missing-function-docstring
"""Display preferences (#122): ranked list length, onboarding, time zone."""

from fastapi.testclient import TestClient

from app.config import get_settings


def _auth(token: str) -> dict:
    return {'Authorization': f"Bearer {token}"}


def _defaults(**overrides) -> dict:
    """
    The payload a user who has set nothing gets back.

    ``time_zone`` is read from settings rather than hardcoded: an unset
    column resolves to the deployment's ``TIME_ZONE``, and pinning a literal
    here would make the suite fail the moment that environment differs.
    """
    body = {
        'ranked_list_length': '25',
        'onboarding_completed': False,
        'time_zone': get_settings().time_zone,
        'shelf_order': ['movies', 'tv', 'games', 'books'],
        'enabled_shelves': ['movies', 'tv', 'games', 'books'],
    }
    body.update(overrides)
    return body


def test_default_is_25(test_client: TestClient):
    body = test_client.get(
        '/v1/users/me/preferences', headers=_auth(test_client.first_user.token)
    ).json()
    assert body == _defaults()
    assert body['shelf_order'] == ['movies', 'tv', 'games', 'books']
    assert body['enabled_shelves'] == ['movies', 'tv', 'games', 'books']


def test_unset_time_zone_defaults_to_new_york(test_client: TestClient):
    """An untouched account reads in Eastern time, without storing a zone."""
    response = test_client.get(
        '/v1/users/me/preferences', headers=_auth(test_client.first_user.token)
    )
    assert response.status_code == 200
    assert response.json()['time_zone'] == 'America/New_York'


def test_set_and_read_back(test_client: TestClient):
    token = test_client.first_user.token
    updated = test_client.put(
        '/v1/users/me/preferences',
        headers=_auth(token),
        json={'ranked_list_length': 'all'},
    )
    assert updated.status_code == 200
    assert updated.json() == _defaults(ranked_list_length='all')

    fetched = test_client.get('/v1/users/me/preferences', headers=_auth(token))
    assert fetched.json() == _defaults(ranked_list_length='all')


def test_set_onboarding_completed(test_client: TestClient):
    token = test_client.first_user.token
    updated = test_client.put(
        '/v1/users/me/preferences',
        headers=_auth(token),
        json={'onboarding_completed': True},
    )
    assert updated.status_code == 200
    assert updated.json() == _defaults(onboarding_completed=True)

    fetched = test_client.get('/v1/users/me/preferences', headers=_auth(token))
    assert fetched.json() == _defaults(onboarding_completed=True)


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
    assert other == _defaults()


def test_set_and_read_back_shelf_preferences(test_client: TestClient):
    token = test_client.first_user.token
    preferences = {
        'shelf_order': ['games', 'books', 'movies', 'tv'],
        'enabled_shelves': ['games', 'movies'],
    }
    updated = test_client.put(
        '/v1/users/me/preferences', headers=_auth(token), json=preferences
    )
    assert updated.status_code == 200
    assert updated.json() == _defaults(**preferences)

    fetched = test_client.get('/v1/users/me/preferences', headers=_auth(token))
    assert fetched.json() == _defaults(**preferences)


def test_unknown_or_duplicate_shelf_id_rejected(test_client: TestClient):
    token = test_client.first_user.token
    unknown = test_client.put(
        '/v1/users/me/preferences',
        headers=_auth(token),
        json={'enabled_shelves': ['movies', 'podcasts']},
    )
    duplicate = test_client.put(
        '/v1/users/me/preferences',
        headers=_auth(token),
        json={'shelf_order': ['movies', 'tv', 'books', 'books']},
    )
    assert unknown.status_code == 422
    assert duplicate.status_code == 422


def test_preferences_require_auth(test_client: TestClient):
    assert test_client.get('/v1/users/me/preferences').status_code == 401
    assert (
        test_client.put(
            '/v1/users/me/preferences', json={'ranked_list_length': '50'}
        ).status_code
        == 401
    )


def test_set_and_read_back_time_zone(test_client: TestClient):
    token = test_client.first_user.token
    updated = test_client.put(
        '/v1/users/me/preferences',
        headers=_auth(token),
        json={'time_zone': 'Asia/Tokyo'},
    )
    assert updated.status_code == 200
    assert updated.json() == _defaults(time_zone='Asia/Tokyo')

    fetched = test_client.get('/v1/users/me/preferences', headers=_auth(token))
    assert fetched.json() == _defaults(time_zone='Asia/Tokyo')


def test_unknown_time_zone_rejected(test_client: TestClient):
    response = test_client.put(
        '/v1/users/me/preferences',
        headers=_auth(test_client.first_user.token),
        json={'time_zone': 'Mars/Olympus_Mons'},
    )
    assert response.status_code == 422
    assert 'Unknown IANA time zone' in response.text


def test_time_zone_is_per_user(test_client: TestClient):
    test_client.put(
        '/v1/users/me/preferences',
        headers=_auth(test_client.first_user.token),
        json={'time_zone': 'Australia/Sydney'},
    )
    other = test_client.get(
        '/v1/users/me/preferences', headers=_auth(test_client.second_user.token)
    ).json()
    assert other['time_zone'] == get_settings().time_zone


def test_setting_one_preference_leaves_the_time_zone_alone(test_client: TestClient):
    """A partial PUT must not reset a zone the user already chose."""
    token = test_client.first_user.token
    test_client.put(
        '/v1/users/me/preferences',
        headers=_auth(token),
        json={'time_zone': 'Europe/London'},
    )
    updated = test_client.put(
        '/v1/users/me/preferences',
        headers=_auth(token),
        json={'ranked_list_length': '50'},
    )
    assert updated.json() == _defaults(
        ranked_list_length='50', time_zone='Europe/London'
    )
