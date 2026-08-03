# pylint: disable=missing-module-docstring, missing-function-docstring
from unittest.mock import patch

from fastapi.testclient import TestClient


def _make_game(test_client: TestClient, title='Breath of the Wild', **extra) -> str:
    headers = {'Authorization': f"Bearer {test_client.admin_user.token}"}
    resp = test_client.post(
        '/v1/games', headers=headers, json={'title': title, **extra}
    )
    assert resp.status_code == 201
    return resp.json()['id']


# --- Global catalog ---
def test_create_game(test_client: TestClient):
    headers = {'Authorization': f"Bearer {test_client.admin_user.token}"}
    response = test_client.post(
        '/v1/games', headers=headers, json={'title': 'Breath of the Wild', 'igdb': 1111}
    )
    assert response.status_code == 201
    data = response.json()
    assert data['title'] == 'Breath of the Wild'
    assert data['igdb'] == 1111


def test_get_games(test_client: TestClient):
    _make_game(test_client)
    response = test_client.get('/v1/games')
    assert response.status_code == 200
    assert len(response.json()) > 0


def test_create_game_unauthenticated(test_client: TestClient):
    response = test_client.post('/v1/games', json={'title': 'Zelda'})
    assert response.status_code == 401


def test_create_game_allowed_for_any_user(test_client: TestClient):
    """Regular users add to the shared catalog via the add-from-search flow."""
    headers = {'Authorization': f"Bearer {test_client.first_user.token}"}
    response = test_client.post('/v1/games', headers=headers, json={'title': 'Zelda'})
    assert response.status_code == 201


def test_create_duplicate_igdb_rejected(test_client: TestClient):
    _make_game(test_client, igdb=1111)
    headers = {'Authorization': f"Bearer {test_client.admin_user.token}"}
    dup = test_client.post(
        '/v1/games', headers=headers, json={'title': 'Zelda again', 'igdb': 1111}
    )
    assert dup.status_code == 400


@patch('app.router.v1.router_games.get_game_detail')
def test_get_game_enriches_on_view(mock_detail, test_client: TestClient):
    game_id = _make_game(test_client)
    mock_detail.return_value = {
        'year': 2017,
        'genre': 'Adventure, RPG',
        'platforms': 'Switch',
        'summary': 'Open-air adventure.',
        'rating': 92.5,
    }
    headers = {'Authorization': f"Bearer {test_client.first_user.token}"}
    resp = test_client.get(f"/v1/games/{game_id}", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data['genre'] == 'Adventure, RPG'
    assert data['platforms'] == 'Switch'
    assert data['summary'] == 'Open-air adventure.'


# --- Search proxy ---
def test_search_games_requires_auth(test_client: TestClient):
    response = test_client.get('/v1/games/search?q=zelda')
    assert response.status_code == 401


def test_search_games_unconfigured_returns_503(test_client: TestClient):
    # No TWITCH_CLIENT_ID/SECRET in the test environment.
    headers = {'Authorization': f"Bearer {test_client.first_user.token}"}
    response = test_client.get('/v1/games/search?q=zelda', headers=headers)
    assert response.status_code == 503


@patch('app.router.v1.router_games.igdb_search_games')
def test_search_games_returns_results(mock_search, test_client: TestClient):
    mock_search.return_value = [
        {
            'igdb': 1234,
            'title': 'Breath of the Wild',
            'year': '2017',
            'platforms': 'Switch',
            'poster_url': 'https://images.igdb.com/x/co3p2d.jpg',
        }
    ]
    headers = {'Authorization': f"Bearer {test_client.first_user.token}"}
    response = test_client.get('/v1/games/search?q=zelda', headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data[0]['igdb'] == 1234
    assert data[0]['platforms'] == 'Switch'


# --- Trackers (Movies-parity lists + 100% flag) ---
def test_mark_first_game_to_rankings_auto_places_at_one(test_client: TestClient):
    """First game into an empty ranked list auto-places at #1 (#289)."""
    game_id = _make_game(test_client)
    headers = {'Authorization': f"Bearer {test_client.first_user.token}"}
    response = test_client.post(
        f"/v1/users/me/games/{game_id}",
        headers=headers,
        json={'on_rankings': True, 'notes': 'GOTY'},
    )
    assert response.status_code == 201
    data = response.json()
    assert data['on_rankings'] is True
    assert data['on_watchlist'] is False
    assert data['rank'] == 1
    assert data['is_100_percent'] is False


def test_mark_second_game_to_rankings_is_unplaced(test_client: TestClient):
    """Once a game is already ranked, the next one lands unplaced pending a duel."""
    first_id = _make_game(test_client)
    second_id = _make_game(test_client, title='Also Ranked')
    headers = {'Authorization': f"Bearer {test_client.first_user.token}"}

    test_client.post(
        f"/v1/users/me/games/{first_id}", headers=headers, json={'on_rankings': True}
    )
    response = test_client.post(
        f"/v1/users/me/games/{second_id}", headers=headers, json={'on_rankings': True}
    )
    assert response.status_code == 201
    data = response.json()
    assert data['on_rankings'] is True
    assert data['rank'] is None


def test_hundred_percent_flag(test_client: TestClient):
    game_id = _make_game(test_client)
    headers = {'Authorization': f"Bearer {test_client.first_user.token}"}
    test_client.post(
        f"/v1/users/me/games/{game_id}", headers=headers, json={'on_rankings': True}
    )
    r = test_client.put(
        f"/v1/users/me/games/{game_id}",
        headers=headers,
        json={'is_100_percent': True},
    )
    assert r.json()['is_100_percent'] is True
    assert r.json()['on_rankings'] is True


def test_lists_are_exclusive(test_client: TestClient):
    """One-home rule (#145): joining Rankings leaves the Watchlist."""
    game_id = _make_game(test_client)
    headers = {'Authorization': f"Bearer {test_client.first_user.token}"}

    # Add to watchlist only.
    r = test_client.post(
        f"/v1/users/me/games/{game_id}",
        headers=headers,
        json={'on_watchlist': True},
    )
    assert r.json()['on_watchlist'] is True
    assert r.json()['on_rankings'] is False

    # Promote to rankings -> leaves the watchlist. It's the only ranked game,
    # so it auto-places at #1 (#289).
    r = test_client.post(
        f"/v1/users/me/games/{game_id}",
        headers=headers,
        json={'on_rankings': True},
    )
    assert r.json()['on_rankings'] is True
    assert r.json()['on_watchlist'] is False
    assert r.json()['rank'] == 1

    # Leave rankings -> on neither list, so the tracker is dropped entirely.
    test_client.put(
        f"/v1/users/me/games/{game_id}",
        headers=headers,
        json={'on_rankings': False},
    )
    listing = test_client.get('/v1/users/me/games', headers=headers).json()
    assert all(t['game']['id'] != game_id for t in listing)


def test_set_game_rank_inserts_and_shifts(test_client: TestClient):
    headers = {'Authorization': f"Bearer {test_client.first_user.token}"}
    ids = []
    for i in range(3):
        gid = _make_game(test_client, title=f"Ranked {i}")
        test_client.post(
            f"/v1/users/me/games/{gid}", headers=headers, json={'on_rankings': True}
        )
        ids.append(gid)
    test_client.put(
        '/v1/users/me/games/rankings/order',
        headers=headers,
        json={'game_ids': ids},
    )

    new_id = _make_game(test_client, title='Inserted')
    test_client.post(
        f"/v1/users/me/games/{new_id}", headers=headers, json={'on_rankings': True}
    )
    resp = test_client.put(
        f"/v1/users/me/games/{new_id}/rank", headers=headers, json={'position': 2}
    )
    assert resp.status_code == 200
    assert resp.json()['rank'] == 2

    listing = test_client.get('/v1/users/me/games', headers=headers).json()
    ranked = sorted(
        [t for t in listing if t['rank'] is not None], key=lambda t: t['rank']
    )
    order = [(t['rank'], t['game']['id']) for t in ranked]
    assert order == [(1, ids[0]), (2, new_id), (3, ids[1]), (4, ids[2])]


def test_reorder_rankings(test_client: TestClient):
    headers = {'Authorization': f"Bearer {test_client.first_user.token}"}
    ids = []
    for i in range(3):
        gid = _make_game(test_client, title=f"Game {i}")
        test_client.post(
            f"/v1/users/me/games/{gid}", headers=headers, json={'on_rankings': True}
        )
        ids.append(gid)

    reordered = list(reversed(ids))
    resp = test_client.put(
        '/v1/users/me/games/rankings/order',
        headers=headers,
        json={'game_ids': reordered},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert [t['game']['id'] for t in data] == reordered
    assert [t['rank'] for t in data] == [1, 2, 3]
