# pylint: disable=missing-module-docstring, missing-function-docstring
from fastapi.testclient import TestClient


def _auth(test_client: TestClient) -> dict:
    return {'Authorization': f'Bearer {test_client.first_user.token}'}


def _make_ranked_movie(test_client: TestClient, title='Heat', imdb='tt0113277') -> str:
    admin = {'Authorization': f'Bearer {test_client.admin_user.token}'}
    movie_id = test_client.post(
        '/v1/movies', headers=admin, json={'title': title, 'imdb': imdb}
    ).json()['id']
    test_client.post(
        f'/v1/users/me/movies/{movie_id}',
        headers=_auth(test_client),
        json={'on_rankings': True},
    )
    test_client.put(
        f'/v1/users/me/movies/{movie_id}/rank',
        headers=_auth(test_client),
        json={'position': 1},
    )
    return movie_id


def _ranked_activity(test_client: TestClient, title: str) -> dict:
    feed = test_client.get('/v1/users/me/activity', headers=_auth(test_client)).json()
    return next(a for a in feed if a['action'] == 'ranked' and a['title'] == title)


def test_notes_edit_does_not_redate_ranking_activity(test_client: TestClient):
    """
    The #141 defect: editing notes bumped updated_at, which re-dated the
    'ranked' activity entry. occurred_at must stay pinned to ranked_at.
    """
    movie_id = _make_ranked_movie(test_client)
    before = _ranked_activity(test_client, 'Heat')['occurred_at']

    response = test_client.put(
        f'/v1/users/me/movies/{movie_id}',
        headers=_auth(test_client),
        json={'notes': 'this note must not bump the ranking date'},
    )
    assert response.status_code == 200
    assert response.json()['notes'].startswith('this note')

    after = _ranked_activity(test_client, 'Heat')['occurred_at']
    assert after == before


def test_reranking_does_redate(test_client: TestClient):
    """
    An actual rank move is a genuine event - the entry re-dates.
    """
    _make_ranked_movie(test_client, title='Heat', imdb='tt0113277')
    _make_ranked_movie(test_client, title='Ronin', imdb='tt0122690')
    before = _ranked_activity(test_client, 'Heat')

    # Move Heat (now #2 after Ronin took #1) back to #1
    movies = test_client.get('/v1/users/me/movies', headers=_auth(test_client)).json()
    heat = next(m for m in movies if m['movie']['title'] == 'Heat')
    test_client.put(
        f"/v1/users/me/movies/{heat['movie']['id']}/rank",
        headers=_auth(test_client),
        json={'position': 1},
    )
    after = _ranked_activity(test_client, 'Heat')
    assert after['occurred_at'] >= before['occurred_at']
    assert after['rank'] == 1


def test_leaving_rankings_clears_the_stamp(test_client: TestClient):
    """
    Demoted trackers drop ranked_at so a stale date can't resurface later.
    """
    movie_id = _make_ranked_movie(test_client)
    test_client.put(
        f'/v1/users/me/movies/{movie_id}',
        headers=_auth(test_client),
        json={'on_watchlist': True},
    )
    tracker = test_client.get(
        f'/v1/users/me/movies/{movie_id}', headers=_auth(test_client)
    ).json()
    assert tracker['rank'] is None
    # Re-promoting starts a fresh history: entry shows as watchlisted now
    feed = test_client.get('/v1/users/me/activity', headers=_auth(test_client)).json()
    entry = next(a for a in feed if a['title'] == 'Heat')
    assert entry['action'] == 'watchlist_added'


def test_watchlist_entry_pinned_to_created_at(test_client: TestClient):
    """
    updated_at is technical, not semantic (#141 follow-up): editing notes on
    a watchlist item must not re-date its 'watchlist_added' feed entry.
    """
    admin = {'Authorization': f'Bearer {test_client.admin_user.token}'}
    movie_id = test_client.post(
        '/v1/movies', headers=admin, json={'title': 'Sneakers', 'imdb': 'tt0105435'}
    ).json()['id']
    test_client.post(
        f'/v1/users/me/movies/{movie_id}',
        headers=_auth(test_client),
        json={'on_watchlist': True},
    )
    feed = test_client.get('/v1/users/me/activity', headers=_auth(test_client)).json()
    before = next(a for a in feed if a['title'] == 'Sneakers')['occurred_at']

    test_client.put(
        f'/v1/users/me/movies/{movie_id}',
        headers=_auth(test_client),
        json={'notes': 'note edit must not re-date the add'},
    )
    feed = test_client.get('/v1/users/me/activity', headers=_auth(test_client)).json()
    after = next(a for a in feed if a['title'] == 'Sneakers')['occurred_at']
    assert after == before
