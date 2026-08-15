"""
Test the one-home rule (#145): watchlist / to-be-ranked / ranked are
mutually exclusive. Full transition matrix on movies; smoke on the other
domains, which share the same patched code paths.
"""

from fastapi.testclient import TestClient


def _auth(test_client: TestClient) -> dict:
    return {'Authorization': f'Bearer {test_client.first_user.token}'}


def _movie(test_client: TestClient, title: str, imdb: str) -> str:
    headers = {'Authorization': f'Bearer {test_client.admin_user.token}'}
    return test_client.post(
        '/v1/movies', headers=headers, json={'title': title, 'imdb': imdb}
    ).json()['id']


def _state(test_client: TestClient, movie_id: str) -> dict:
    return test_client.get(
        f'/v1/users/me/movies/{movie_id}', headers=_auth(test_client)
    ).json()


def test_ranking_a_watchlisted_movie_leaves_the_watchlist(test_client: TestClient):
    """
    watchlist → to-rank via POST merge: rankings wins, watchlist cleared.
    """
    movie_id = _movie(test_client, 'Inception', 'tt1375666')
    test_client.post(
        f'/v1/users/me/movies/{movie_id}',
        headers=_auth(test_client),
        json={'on_watchlist': True},
    )
    test_client.post(
        f'/v1/users/me/movies/{movie_id}',
        headers=_auth(test_client),
        json={'on_rankings': True},
    )
    state = _state(test_client, movie_id)
    assert state['on_rankings'] is True
    assert state['on_watchlist'] is False


def test_requesting_both_lists_at_once_rankings_wins(test_client: TestClient):
    """
    A single request asking for both homes resolves to Rankings.
    """
    movie_id = _movie(test_client, 'Alien', 'tt0078748')
    response = test_client.post(
        f'/v1/users/me/movies/{movie_id}',
        headers=_auth(test_client),
        json={'on_watchlist': True, 'on_rankings': True},
    )
    assert response.json()['on_rankings'] is True
    assert response.json()['on_watchlist'] is False


def test_watchlisting_a_ranked_movie_unranks_and_closes_the_gap(
    test_client: TestClient,
):
    """
    ranked → watchlist via PUT: leaves rankings, rank cleared, the movie
    below shifts up into the vacated slot.
    """
    first = _movie(test_client, 'Heat', 'tt0113277')
    second = _movie(test_client, 'Ronin', 'tt0122690')
    for movie_id in (first, second):
        test_client.post(
            f'/v1/users/me/movies/{movie_id}',
            headers=_auth(test_client),
            json={'on_rankings': True},
        )
        test_client.put(
            f'/v1/users/me/movies/{movie_id}/rank',
            headers=_auth(test_client),
            json={'position': 1 if movie_id == first else 2},
        )

    test_client.put(
        f'/v1/users/me/movies/{first}',
        headers=_auth(test_client),
        json={'on_watchlist': True},
    )
    demoted = _state(test_client, first)
    assert demoted['on_watchlist'] is True
    assert demoted['on_rankings'] is False
    assert demoted['rank'] is None
    # The former #2 closed the gap
    assert _state(test_client, second)['rank'] == 1


def test_setting_rank_directly_leaves_the_watchlist(test_client: TestClient):
    """
    watchlist → ranked in one hop via the rank endpoint.
    """
    movie_id = _movie(test_client, 'Sneakers', 'tt0105435')
    test_client.post(
        f'/v1/users/me/movies/{movie_id}',
        headers=_auth(test_client),
        json={'on_watchlist': True},
    )
    test_client.put(
        f'/v1/users/me/movies/{movie_id}/rank',
        headers=_auth(test_client),
        json={'position': 1},
    )
    state = _state(test_client, movie_id)
    assert state['rank'] == 1
    assert state['on_rankings'] is True
    assert state['on_watchlist'] is False


def test_reorder_clears_watchlist_membership(test_client: TestClient):
    """
    Drag-and-drop reorder also asserts the one-home rule on every row.
    """
    movie_id = _movie(test_client, 'Gattaca', 'tt0119177')
    test_client.post(
        f'/v1/users/me/movies/{movie_id}',
        headers=_auth(test_client),
        json={'on_watchlist': True, 'on_rankings': True},
    )
    test_client.put(
        '/v1/users/me/movies/rankings/order',
        headers=_auth(test_client),
        json={'movie_ids': [movie_id]},
    )
    state = _state(test_client, movie_id)
    assert state['rank'] == 1
    assert state['on_watchlist'] is False


def _smoke_domain(test_client: TestClient, noun: str, create_json: dict):
    """Shared smoke: watchlist item promoted to rankings leaves watchlist."""
    admin = {'Authorization': f'Bearer {test_client.admin_user.token}'}
    item_id = test_client.post(f'/v1/{noun}', headers=admin, json=create_json).json()[
        'id'
    ]
    test_client.post(
        f'/v1/users/me/{noun}/{item_id}',
        headers=_auth(test_client),
        json={'on_watchlist': True},
    )
    test_client.post(
        f'/v1/users/me/{noun}/{item_id}',
        headers=_auth(test_client),
        json={'on_rankings': True},
    )
    state = test_client.get(
        f'/v1/users/me/{noun}/{item_id}', headers=_auth(test_client)
    ).json()
    assert state['on_rankings'] is True
    assert state['on_watchlist'] is False


def test_tv_books_games_share_the_rule(test_client: TestClient):
    """
    The other domains run the same patched paths - one promotion each.
    """
    _smoke_domain(test_client, 'tv-shows', {'title': 'Severance', 'imdb': 'tt11280740'})
    _smoke_domain(test_client, 'books', {'title': 'Dune', 'isbn': '9780441172719'})
    _smoke_domain(test_client, 'games', {'title': 'Hades', 'igdb': 113112})
