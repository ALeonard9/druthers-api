"""
Test the completed-date rule (#159): defaults to the day an item enters
Rankings, explicit values win, editable and clearable afterwards.
"""

from fastapi.testclient import TestClient

from app.services.tracker_rules import utc_now


def _auth(test_client: TestClient) -> dict:
    return {'Authorization': f'Bearer {test_client.first_user.token}'}


def _movie(test_client: TestClient, title='Heat', imdb='tt0113277') -> str:
    admin = {'Authorization': f'Bearer {test_client.admin_user.token}'}
    return test_client.post(
        '/v1/movies', headers=admin, json={'title': title, 'imdb': imdb}
    ).json()['id']


def test_entering_rankings_stamps_today(test_client: TestClient):
    """
    Adding to Rankings means "I finished this" - completed_at defaults to
    today.
    """
    movie_id = _movie(test_client)
    body = test_client.post(
        f'/v1/users/me/movies/{movie_id}',
        headers=_auth(test_client),
        json={'on_rankings': True},
    ).json()
    assert body['completed_at'] == utc_now().date().isoformat()


def test_watchlist_does_not_stamp(test_client: TestClient):
    """
    Queueing something is not finishing it.
    """
    movie_id = _movie(test_client)
    body = test_client.post(
        f'/v1/users/me/movies/{movie_id}',
        headers=_auth(test_client),
        json={'on_watchlist': True},
    ).json()
    assert body['completed_at'] is None


def test_explicit_date_wins_over_the_default(test_client: TestClient):
    """
    Supplying a date in the same request that enters Rankings keeps it.
    """
    movie_id = _movie(test_client)
    body = test_client.post(
        f'/v1/users/me/movies/{movie_id}',
        headers=_auth(test_client),
        json={'on_rankings': True, 'completed_at': '2019-06-01'},
    ).json()
    assert body['completed_at'] == '2019-06-01'


def test_date_is_editable_and_clearable(test_client: TestClient):
    """
    The detail page can change or clear the date after the fact.
    """
    movie_id = _movie(test_client)
    test_client.post(
        f'/v1/users/me/movies/{movie_id}',
        headers=_auth(test_client),
        json={'on_rankings': True},
    )
    edited = test_client.put(
        f'/v1/users/me/movies/{movie_id}',
        headers=_auth(test_client),
        json={'completed_at': '2020-12-25'},
    ).json()
    assert edited['completed_at'] == '2020-12-25'

    cleared = test_client.put(
        f'/v1/users/me/movies/{movie_id}',
        headers=_auth(test_client),
        json={'completed_at': None},
    ).json()
    assert cleared['completed_at'] is None


def test_reentering_rankings_does_not_overwrite_history(test_client: TestClient):
    """
    Demote to watchlist and re-promote: an existing date must survive.
    """
    movie_id = _movie(test_client)
    test_client.post(
        f'/v1/users/me/movies/{movie_id}',
        headers=_auth(test_client),
        json={'on_rankings': True, 'completed_at': '2018-03-15'},
    )
    test_client.put(
        f'/v1/users/me/movies/{movie_id}',
        headers=_auth(test_client),
        json={'on_watchlist': True},
    )
    body = test_client.post(
        f'/v1/users/me/movies/{movie_id}',
        headers=_auth(test_client),
        json={'on_rankings': True},
    ).json()
    assert body['completed_at'] == '2018-03-15'


def test_rank_endpoint_one_hop_stamps(test_client: TestClient):
    """
    watchlist -> ranked directly via the rank endpoint also counts as
    finishing.
    """
    movie_id = _movie(test_client)
    test_client.post(
        f'/v1/users/me/movies/{movie_id}',
        headers=_auth(test_client),
        json={'on_watchlist': True},
    )
    body = test_client.put(
        f'/v1/users/me/movies/{movie_id}/rank',
        headers=_auth(test_client),
        json={'position': 1},
    ).json()
    assert body['completed_at'] == utc_now().date().isoformat()


def test_other_domains_share_the_rule(test_client: TestClient):
    """
    TV, books, and games run the same patched paths.
    """
    admin = {'Authorization': f'Bearer {test_client.admin_user.token}'}
    cases = [
        ('tv-shows', {'title': 'Severance', 'imdb': 'tt11280740'}),
        ('books', {'title': 'Dune', 'isbn': '9780441172719'}),
        ('games', {'title': 'Hades', 'igdb': 113112}),
    ]
    for noun, payload in cases:
        item_id = test_client.post(f'/v1/{noun}', headers=admin, json=payload).json()[
            'id'
        ]
        body = test_client.post(
            f'/v1/users/me/{noun}/{item_id}',
            headers=_auth(test_client),
            json={'on_rankings': True},
        ).json()
        assert body['completed_at'] == utc_now().date().isoformat(), noun
