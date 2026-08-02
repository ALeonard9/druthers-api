# pylint: disable=missing-module-docstring, missing-function-docstring
from fastapi.testclient import TestClient


def _auth(token: str) -> dict:
    return {'Authorization': f'Bearer {token}'}


def _rank_a_movie(test_client: TestClient, token: str, title='Heat', imdb='tt0113277'):
    admin = _auth(test_client.admin_user.token)
    movie_id = test_client.post(
        '/v1/movies', headers=admin, json={'title': title, 'imdb': imdb, 'year': 1995}
    ).json()['id']
    test_client.post(
        f'/v1/users/me/movies/{movie_id}',
        headers=_auth(token),
        json={'on_rankings': True, 'notes': 'private note!'},
    )
    test_client.put(
        f'/v1/users/me/movies/{movie_id}/rank',
        headers=_auth(token),
        json={'position': 1},
    )


def _watchlist_a_movie(
    test_client: TestClient, token: str, title='Sicario', imdb='tt3397884'
):
    admin = _auth(test_client.admin_user.token)
    movie_id = test_client.post(
        '/v1/movies', headers=admin, json={'title': title, 'imdb': imdb, 'year': 2015}
    ).json()['id']
    test_client.post(
        f'/v1/users/me/movies/{movie_id}',
        headers=_auth(token),
        json={'on_watchlist': True, 'notes': 'private note!'},
    )


def test_defaults_are_fully_private(test_client: TestClient):
    body = test_client.get(
        '/v1/users/me/visibility', headers=_auth(test_client.first_user.token)
    ).json()
    assert body['handle'] is None
    assert not any(
        body[f]
        for f in (
            'public_movies',
            'public_tv',
            'public_books',
            'public_games',
            'public_watchlist_movies',
            'public_watchlist_tv',
            'public_watchlist_books',
            'public_watchlist_games',
        )
    )


def test_public_flag_requires_a_handle(test_client: TestClient):
    response = test_client.put(
        '/v1/users/me/visibility',
        headers=_auth(test_client.first_user.token),
        json={'public_movies': True},
    )
    assert response.status_code == 422
    assert 'handle' in response.json()['message'].lower()


def test_handle_validation_and_uniqueness(test_client: TestClient):
    token = test_client.first_user.token
    assert (
        test_client.put(
            '/v1/users/me/visibility',
            headers=_auth(token),
            json={'handle': 'No Spaces!'},
        ).status_code
        == 422
    )
    assert (
        test_client.put(
            '/v1/users/me/visibility', headers=_auth(token), json={'handle': 'settings'}
        ).status_code
        == 409
    )
    assert (
        test_client.put(
            '/v1/users/me/visibility', headers=_auth(token), json={'handle': 'Avery'}
        ).json()['handle']
        == 'avery'
    )
    # Second user can't take it
    assert (
        test_client.put(
            '/v1/users/me/visibility',
            headers=_auth(test_client.second_user.token),
            json={'handle': 'avery'},
        ).status_code
        == 409
    )


def test_public_profile_exposes_only_public_ranked_lists(test_client: TestClient):
    token = test_client.first_user.token
    _rank_a_movie(test_client, token)
    test_client.put(
        '/v1/users/me/visibility',
        headers=_auth(token),
        json={'handle': 'avery', 'public_movies': True},
    )

    body = test_client.get('/v1/public/avery').json()
    assert body['handle'] == 'avery'
    assert [s['category'] for s in body['shelves']] == ['Movies']
    item = body['shelves'][0]['items'][0]
    assert item == {
        'rank': 1,
        'title': 'Heat',
        'year': 1995,
        'poster_url': None,
    }
    # Nothing private leaks anywhere in the payload
    flat = str(body)
    assert 'private note!' not in flat
    assert 'on_watchlist' not in flat
    assert test_client.first_user.email not in flat


def test_private_and_unknown_profiles_404_identically(test_client: TestClient):
    token = test_client.second_user.token
    test_client.put(
        '/v1/users/me/visibility', headers=_auth(token), json={'handle': 'ghost'}
    )
    private = test_client.get('/v1/public/ghost')
    unknown = test_client.get('/v1/public/nobody-here')
    assert private.status_code == unknown.status_code == 404
    assert private.json() == unknown.json()


def test_toggling_a_category_off_removes_it(test_client: TestClient):
    token = test_client.first_user.token
    _rank_a_movie(test_client, token, title='Ronin', imdb='tt0122690')
    test_client.put(
        '/v1/users/me/visibility',
        headers=_auth(token),
        json={'handle': 'avery', 'public_movies': True},
    )
    assert test_client.get('/v1/public/avery').status_code == 200
    test_client.put(
        '/v1/users/me/visibility', headers=_auth(token), json={'public_movies': False}
    )
    assert test_client.get('/v1/public/avery').status_code == 404


def test_watchlist_flag_alone_exposes_nothing(test_client: TestClient):
    # Watchlist visibility (#236) is independent of the ranked-list flag to
    # set, but only takes effect once the ranked-list flag is also on.
    token = test_client.first_user.token
    _rank_a_movie(test_client, token)
    _watchlist_a_movie(test_client, token)
    test_client.put(
        '/v1/users/me/visibility',
        headers=_auth(token),
        json={'handle': 'avery', 'public_watchlist_movies': True},
    )

    assert test_client.get('/v1/public/avery').status_code == 404


def test_watchlist_shown_only_when_both_flags_set(test_client: TestClient):
    token = test_client.first_user.token
    _rank_a_movie(test_client, token)
    _watchlist_a_movie(test_client, token)
    test_client.put(
        '/v1/users/me/visibility',
        headers=_auth(token),
        json={'handle': 'avery', 'public_movies': True},
    )

    # Ranked-list flag alone: no watchlist key at all.
    shelf = test_client.get('/v1/public/avery').json()['shelves'][0]
    assert 'watchlist' not in shelf

    test_client.put(
        '/v1/users/me/visibility',
        headers=_auth(token),
        json={'public_watchlist_movies': True},
    )

    shelf = test_client.get('/v1/public/avery').json()['shelves'][0]
    assert shelf['watchlist'] == [
        {'title': 'Sicario', 'year': 2015, 'poster_url': None}
    ]
    # Redacted the same way ranked items are: no notes, no watch state.
    flat = str(shelf['watchlist'])
    assert 'private note!' not in flat
    assert 'on_watchlist' not in flat
