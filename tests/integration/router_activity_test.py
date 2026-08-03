# pylint: disable=missing-module-docstring, missing-function-docstring
from fastapi.testclient import TestClient


def _make_movie(test_client: TestClient, imdb='tt1375666', title='Inception') -> str:
    headers = {'Authorization': f"Bearer {test_client.admin_user.token}"}
    resp = test_client.post(
        '/v1/movies', headers=headers, json={'title': title, 'imdb': imdb}
    )
    assert resp.status_code == 201
    return resp.json()['id']


def _make_show(test_client: TestClient, title='Breaking Bad', **extra) -> str:
    headers = {'Authorization': f"Bearer {test_client.admin_user.token}"}
    resp = test_client.post(
        '/v1/tv-shows', headers=headers, json={'title': title, **extra}
    )
    assert resp.status_code == 201
    return resp.json()['id']


def _make_episode(test_client: TestClient, show_id: str, title='Pilot', **extra) -> str:
    headers = {'Authorization': f"Bearer {test_client.admin_user.token}"}
    resp = test_client.post(
        f"/v1/tv-shows/{show_id}/episodes",
        headers=headers,
        json={'title': title, 'season': 1, 'season_number': 1, **extra},
    )
    assert resp.status_code == 201
    return resp.json()['id']


def _make_game(test_client: TestClient, title='Breath of the Wild', **extra) -> str:
    headers = {'Authorization': f"Bearer {test_client.admin_user.token}"}
    resp = test_client.post(
        '/v1/games', headers=headers, json={'title': title, **extra}
    )
    assert resp.status_code == 201
    return resp.json()['id']


def _make_book(test_client: TestClient, title='Dune', **extra) -> str:
    headers = {'Authorization': f"Bearer {test_client.admin_user.token}"}
    resp = test_client.post(
        '/v1/books', headers=headers, json={'title': title, **extra}
    )
    assert resp.status_code == 201
    return resp.json()['id']


# --- Activity Log ---
def test_activity_includes_ranked_movie(test_client: TestClient):
    movie_id = _make_movie(test_client)
    headers = {'Authorization': f"Bearer {test_client.first_user.token}"}
    test_client.post(
        f"/v1/users/me/movies/{movie_id}", headers=headers, json={'on_rankings': True}
    )
    test_client.put(
        f"/v1/users/me/movies/{movie_id}/rank", headers=headers, json={'position': 1}
    )

    resp = test_client.get('/v1/users/me/activity', headers=headers)
    assert resp.status_code == 200
    items = resp.json()
    movie_items = [i for i in items if i['category'] == 'movie']
    assert len(movie_items) == 1
    assert movie_items[0]['action'] == 'ranked'
    assert movie_items[0]['rank'] == 1
    assert movie_items[0]['title'] == 'Inception'


def test_activity_watchlist_add_not_ranked(test_client: TestClient):
    book_id = _make_book(test_client)
    headers = {'Authorization': f"Bearer {test_client.first_user.token}"}
    test_client.post(
        f"/v1/users/me/books/{book_id}", headers=headers, json={'on_watchlist': True}
    )

    resp = test_client.get('/v1/users/me/activity', headers=headers)
    items = resp.json()
    book_items = [i for i in items if i['category'] == 'book']
    assert len(book_items) == 1
    assert book_items[0]['action'] == 'watchlist_added'
    assert book_items[0]['rank'] is None


def test_activity_includes_watched_episode(test_client: TestClient):
    show_id = _make_show(test_client)
    episode_id = _make_episode(test_client, show_id)
    headers = {'Authorization': f"Bearer {test_client.first_user.token}"}
    test_client.post(f"/v1/users/me/episodes/{episode_id}", headers=headers)

    resp = test_client.get('/v1/users/me/activity', headers=headers)
    items = resp.json()
    ep_items = [i for i in items if i['category'] == 'tv_episode']
    assert len(ep_items) == 1
    assert ep_items[0]['action'] == 'watched_episode'
    assert ep_items[0]['title'] == 'Breaking Bad'
    assert 'S1E1' in ep_items[0]['subtitle']
    assert ep_items[0]['entity_id'] == show_id


def test_activity_filters_by_category(test_client: TestClient):
    movie_id = _make_movie(test_client)
    game_id = _make_game(test_client)
    headers = {'Authorization': f"Bearer {test_client.first_user.token}"}
    test_client.post(
        f"/v1/users/me/movies/{movie_id}", headers=headers, json={'on_watchlist': True}
    )
    test_client.post(
        f"/v1/users/me/games/{game_id}", headers=headers, json={'on_watchlist': True}
    )

    resp = test_client.get(
        '/v1/users/me/activity', headers=headers, params={'category': 'game'}
    )
    items = resp.json()
    assert len(items) == 1
    assert items[0]['category'] == 'game'


def test_activity_untracked_items_excluded(test_client: TestClient):
    _make_movie(test_client)
    headers = {'Authorization': f"Bearer {test_client.first_user.token}"}
    resp = test_client.get('/v1/users/me/activity', headers=headers)
    assert resp.status_code == 200
    assert resp.json() == []


def test_activity_requires_auth(test_client: TestClient):
    resp = test_client.get('/v1/users/me/activity')
    assert resp.status_code == 401


# --- Bored ---
def test_bored_picks_from_watchlists(test_client: TestClient):
    movie_id = _make_movie(test_client)
    headers = {'Authorization': f"Bearer {test_client.first_user.token}"}
    test_client.post(
        f"/v1/users/me/movies/{movie_id}", headers=headers, json={'on_watchlist': True}
    )

    resp = test_client.get('/v1/users/me/bored', headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data['pool_size'] == 1
    assert data['pick']['category'] == 'movie'
    assert data['pick']['entity_id'] == movie_id


def test_bored_excludes_ranked_or_completed_items(test_client: TestClient):
    """Only items still on a watchlist are candidates — ranked ones are done."""
    movie_id = _make_movie(test_client)
    headers = {'Authorization': f"Bearer {test_client.first_user.token}"}
    test_client.post(
        f"/v1/users/me/movies/{movie_id}", headers=headers, json={'on_rankings': True}
    )

    resp = test_client.get('/v1/users/me/bored', headers=headers)
    assert resp.status_code == 404


def test_bored_404_when_nothing_tracked(test_client: TestClient):
    headers = {'Authorization': f"Bearer {test_client.first_user.token}"}
    resp = test_client.get('/v1/users/me/bored', headers=headers)
    assert resp.status_code == 404


def test_bored_exclude_param_avoids_repeat(test_client: TestClient):
    movie_id = _make_movie(test_client)
    book_id = _make_book(test_client)
    headers = {'Authorization': f"Bearer {test_client.first_user.token}"}
    test_client.post(
        f"/v1/users/me/movies/{movie_id}", headers=headers, json={'on_watchlist': True}
    )
    test_client.post(
        f"/v1/users/me/books/{book_id}", headers=headers, json={'on_watchlist': True}
    )

    resp = test_client.get(
        '/v1/users/me/bored', headers=headers, params={'exclude': movie_id}
    )
    assert resp.status_code == 200
    assert resp.json()['pick']['entity_id'] == book_id


def test_bored_requires_auth(test_client: TestClient):
    resp = test_client.get('/v1/users/me/bored')
    assert resp.status_code == 401
