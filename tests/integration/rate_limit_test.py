"""
Test the abuse-resistance layer (#148): password-login kill switch, per-IP
auth limits, per-user search limits, and the daily catalog-add cap.
"""

from unittest.mock import patch

from fastapi.testclient import TestClient

from app.config import Settings
from app.services import rate_limit

ENABLED = {'env': 'github', 'rate_limits_enabled': True}


def _auth(test_client: TestClient) -> dict:
    return {'Authorization': f'Bearer {test_client.first_user.token}'}


@patch('app.auth.authentication.get_settings')
def test_password_login_kill_switch(mock_settings, test_client: TestClient):
    """
    DISABLE_PASSWORD_LOGIN turns /v1/auth/token off with a clear 403.
    """
    mock_settings.return_value = Settings(env='github', disable_password_login=True)
    response = test_client.post(
        '/v1/auth/token',
        data={'username': test_client.first_user.email, 'password': 'whatever'},
    )
    assert response.status_code == 403
    assert 'Google or an API key' in response.json()['message']


@patch('app.services.rate_limit.get_settings')
def test_auth_attempts_are_rate_limited_per_ip(mock_settings, test_client: TestClient):
    """
    The 4th sign-in attempt inside the window gets a 429 with Retry-After.
    """
    rate_limit.reset()
    mock_settings.return_value = Settings(**ENABLED, rate_limit_auth=3)
    for _ in range(3):
        response = test_client.post(
            '/v1/auth/token', data={'username': 'x@y.z', 'password': 'wrong'}
        )
        assert response.status_code == 404
    response = test_client.post(
        '/v1/auth/token', data={'username': 'x@y.z', 'password': 'wrong'}
    )
    assert response.status_code == 429
    assert response.headers['retry-after'] == '300'
    rate_limit.reset()


@patch('app.router.v1.router_movies.tmdb_search_movies')
@patch('app.services.rate_limit.get_settings')
def test_search_is_rate_limited_per_user(
    mock_settings, mock_search, test_client: TestClient
):
    """
    Search proxies burn external quotas — the per-user cap kicks in.
    """
    rate_limit.reset()
    mock_settings.return_value = Settings(**ENABLED, rate_limit_search=2)
    mock_search.return_value = [
        {
            'tmdb': 1,
            'title': 'X',
            'year': '2020',
            'poster_url': None,
            'type': 'movie',
        }
    ]
    for _ in range(2):
        assert (
            test_client.get(
                '/v1/movies/search?q=x', headers=_auth(test_client)
            ).status_code
            == 200
        )
    assert (
        test_client.get('/v1/movies/search?q=x', headers=_auth(test_client)).status_code
        == 429
    )
    rate_limit.reset()


@patch('app.services.rate_limit.get_settings')
def test_catalog_adds_have_a_daily_cap(mock_settings, test_client: TestClient):
    """
    The N+1th catalog creation of the day is refused.
    """
    rate_limit.reset()
    mock_settings.return_value = Settings(**ENABLED, catalog_add_daily_cap=2)
    headers = {'Authorization': f'Bearer {test_client.admin_user.token}'}
    for i in range(2):
        response = test_client.post(
            '/v1/movies', headers=headers, json={'title': f'M{i}', 'imdb': f'tt55{i}'}
        )
        assert response.status_code == 201
    response = test_client.post(
        '/v1/movies', headers=headers, json={'title': 'M3', 'imdb': 'tt553'}
    )
    assert response.status_code == 429
    assert 'today' in response.json()['message']
    rate_limit.reset()


@patch('app.services.rate_limit.get_settings')
def test_friend_requests_are_rate_limited_per_user(
    mock_settings, test_client: TestClient
):
    """
    Friend requests are the only write that names another user's handle, so
    the cap is also the brake on probing for who exists (#275). Attempts are
    counted before the lookup — a request to a handle nobody has costs the
    caller exactly as much as one that lands.
    """
    rate_limit.reset()
    mock_settings.return_value = Settings(**ENABLED, rate_limit_friend_requests=2)
    for _ in range(2):
        response = test_client.post(
            '/v1/users/me/friends/requests',
            headers=_auth(test_client),
            json={'handle': 'nobody-at-all'},
        )
        assert response.status_code == 202
    response = test_client.post(
        '/v1/users/me/friends/requests',
        headers=_auth(test_client),
        json={'handle': 'nobody-at-all'},
    )
    assert response.status_code == 429
    assert response.headers['retry-after'] == '3600'
    rate_limit.reset()


@patch('app.services.rate_limit.get_settings')
def test_follows_are_rate_limited_per_user(mock_settings, test_client: TestClient):
    """
    Following (#276) targets only already-public profiles, so this cap is
    purely a spam brake rather than a probing defense — but it still counts
    attempts before the lookup, same as the friend-request cap does.
    """
    rate_limit.reset()
    mock_settings.return_value = Settings(**ENABLED, rate_limit_follows=2)
    for _ in range(2):
        response = test_client.put(
            '/v1/users/me/following/nobody-at-all', headers=_auth(test_client)
        )
        assert response.status_code == 404
    response = test_client.put(
        '/v1/users/me/following/nobody-at-all', headers=_auth(test_client)
    )
    assert response.status_code == 429
    assert response.headers['retry-after'] == '3600'
    rate_limit.reset()


def test_limits_are_off_in_ci_by_default(test_client: TestClient):
    """
    Without the explicit enable, CI/local traffic is never throttled — the
    rest of the suite depends on this.
    """
    rate_limit.reset()
    for _ in range(15):
        response = test_client.post(
            '/v1/auth/token', data={'username': 'x@y.z', 'password': 'wrong'}
        )
        assert response.status_code == 404
