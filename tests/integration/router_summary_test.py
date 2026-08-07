# pylint: disable=missing-module-docstring, missing-function-docstring
from fastapi.testclient import TestClient


def _auth(token: str) -> dict:
    return {'Authorization': f'Bearer {token}'}


def _add_movie(test_client: TestClient, title: str, imdb: str, year: int = 1999) -> str:
    return test_client.post(
        '/v1/movies',
        headers=_auth(test_client.admin_user.token),
        json={'title': title, 'imdb': imdb, 'year': year},
    ).json()['id']


def _rank(test_client: TestClient, token: str, movie_id: str, position: int):
    test_client.post(
        f'/v1/users/me/movies/{movie_id}',
        headers=_auth(token),
        json={'on_rankings': True},
    )
    test_client.put(
        f'/v1/users/me/movies/{movie_id}/rank',
        headers=_auth(token),
        json={'position': position},
    )


def _queue(test_client: TestClient, token: str, movie_id: str):
    test_client.post(
        f'/v1/users/me/movies/{movie_id}',
        headers=_auth(token),
        json={'on_watchlist': True},
    )


def _shelf(body: dict, category: str) -> dict:
    return next(s for s in body['shelves'] if s['category'] == category)


def test_summary_requires_auth(test_client: TestClient):
    assert test_client.get('/v1/users/me/summary').status_code == 401


def test_empty_summary_has_all_four_shelves(test_client: TestClient):
    body = test_client.get(
        '/v1/users/me/summary', headers=_auth(test_client.first_user.token)
    ).json()
    assert [s['category'] for s in body['shelves']] == [
        'movies',
        'tv',
        'books',
        'games',
    ]
    assert body['total_ranked'] == 0
    assert body['profile_public'] is False
    assert all(s['top'] == [] for s in body['shelves'])


def test_top_is_ranked_order_and_counts_are_separate(test_client: TestClient):
    token = test_client.first_user.token
    # Rank three, queue two.
    for i, title in enumerate(['Heat', 'Ronin', 'Sneakers'], start=1):
        _rank(test_client, token, _add_movie(test_client, title, f'tt000{i}'), i)
    for i, title in enumerate(['Alien', 'Aliens'], start=4):
        _queue(test_client, token, _add_movie(test_client, title, f'tt000{i}'))

    movies = _shelf(
        test_client.get('/v1/users/me/summary', headers=_auth(token)).json(),
        'movies',
    )
    assert [e['title'] for e in movies['top']] == ['Heat', 'Ronin', 'Sneakers']
    assert [e['rank'] for e in movies['top']] == [1, 2, 3]
    assert movies['ranked_count'] == 3
    assert movies['queued_count'] == 2


def test_top_is_capped_at_five(test_client: TestClient):
    token = test_client.first_user.token
    for i in range(1, 8):
        _rank(test_client, token, _add_movie(test_client, f'Film {i}', f'tt10{i}'), i)

    body = test_client.get('/v1/users/me/summary', headers=_auth(token)).json()
    assert len(_shelf(body, 'movies')['top']) == 5
    assert _shelf(body, 'movies')['ranked_count'] == 7
    # The cap is a ceiling, not just a default.
    assert (
        test_client.get('/v1/users/me/summary?top=99', headers=_auth(token)).status_code
        == 422
    )
    assert (
        len(
            _shelf(
                test_client.get(
                    '/v1/users/me/summary?top=2', headers=_auth(token)
                ).json(),
                'movies',
            )['top']
        )
        == 2
    )


def test_summary_is_per_user(test_client: TestClient):
    _rank(
        test_client,
        test_client.first_user.token,
        _add_movie(test_client, 'Heat', 'tt0113277'),
        1,
    )
    body = test_client.get(
        '/v1/users/me/summary', headers=_auth(test_client.second_user.token)
    ).json()
    assert body['total_ranked'] == 0


def test_needs_onboarding_true_for_empty_unfinished_account(test_client: TestClient):
    body = test_client.get(
        '/v1/users/me/summary', headers=_auth(test_client.first_user.token)
    ).json()
    assert body['onboarding_completed'] is False
    assert body['needs_onboarding'] is True


def test_needs_onboarding_false_once_an_item_exists(test_client: TestClient):
    # An account with items but a still-false flag (e.g. migrated/seeded data,
    # or pre-existing users who never ran the wizard) must not be routed into
    # onboarding just because the flag defaults false.
    token = test_client.first_user.token
    _queue(test_client, token, _add_movie(test_client, 'Heat', 'tt0113277'))

    body = test_client.get('/v1/users/me/summary', headers=_auth(token)).json()
    assert body['onboarding_completed'] is False
    assert body['needs_onboarding'] is False


def test_needs_onboarding_false_once_flag_set_even_at_zero_items(
    test_client: TestClient,
):
    # A user who skips the wizard without adding anything still flips the
    # flag; needs_onboarding must honor that so the home <-> onboarding
    # redirect doesn't loop.
    token = test_client.first_user.token
    test_client.put(
        '/v1/users/me/preferences',
        headers=_auth(token),
        json={'onboarding_completed': True},
    )

    body = test_client.get('/v1/users/me/summary', headers=_auth(token)).json()
    assert body['onboarding_completed'] is True
    assert body['needs_onboarding'] is False


def test_profile_public_tracks_handle_and_flags(test_client: TestClient):
    token = test_client.first_user.token
    _rank(test_client, token, _add_movie(test_client, 'Heat', 'tt0113277'), 1)

    # A handle alone doesn't make the profile resolve.
    test_client.put(
        '/v1/users/me/visibility', headers=_auth(token), json={'handle': 'avery'}
    )
    body = test_client.get('/v1/users/me/summary', headers=_auth(token)).json()
    assert body['handle'] == 'avery'
    assert body['profile_public'] is False

    test_client.put(
        '/v1/users/me/visibility',
        headers=_auth(token),
        json={'visibility_profile': 'public', 'visibility_movies': 'public'},
    )
    body = test_client.get('/v1/users/me/summary', headers=_auth(token)).json()
    assert body['profile_public'] is True
    assert _shelf(body, 'movies')['public'] is True
    assert _shelf(body, 'tv')['public'] is False
    # profile_public is the same rule the public endpoint enforces.
    assert test_client.get('/v1/public/avery').status_code == 200
