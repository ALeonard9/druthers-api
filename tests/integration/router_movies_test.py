# pylint: disable=missing-module-docstring, missing-function-docstring
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.config import Settings
from app.services import watch_providers


def test_create_movie(test_client: TestClient):
    admin_token = test_client.admin_user.token
    headers = {'Authorization': f"Bearer {admin_token}"}
    response = test_client.post(
        '/v1/movies', headers=headers, json={'title': 'Inception', 'imdb': 'tt1375666'}
    )
    assert response.status_code == 201
    data = response.json()
    assert data['title'] == 'Inception'
    assert data['imdb'] == 'tt1375666'


def test_get_movies(test_client: TestClient):
    admin_token = test_client.admin_user.token
    headers = {'Authorization': f"Bearer {admin_token}"}
    test_client.post(
        '/v1/movies', headers=headers, json={'title': 'Inception', 'imdb': 'tt1375666'}
    )

    response = test_client.get('/v1/movies')
    assert response.status_code == 200
    data = response.json()
    assert len(data) > 0


def _make_movie(test_client: TestClient, imdb='tt1375666', title='Inception') -> str:
    headers = {'Authorization': f"Bearer {test_client.admin_user.token}"}
    resp = test_client.post(
        '/v1/movies', headers=headers, json={'title': title, 'imdb': imdb}
    )
    return resp.json()['id']


def test_mark_first_movie_to_rankings_auto_places_at_one(test_client: TestClient):
    """First movie into an empty ranked list auto-places at #1 (#289)."""
    movie_id = _make_movie(test_client)
    user_headers = {'Authorization': f"Bearer {test_client.first_user.token}"}

    response = test_client.post(
        f"/v1/users/me/movies/{movie_id}",
        headers=user_headers,
        json={'on_rankings': True, 'notes': 'Mind-bending!'},
    )
    assert response.status_code == 201
    data = response.json()
    assert data['on_rankings'] is True
    assert data['on_watchlist'] is False
    assert data['rank'] == 1
    assert data['notes'] == 'Mind-bending!'


def test_mark_second_movie_to_rankings_is_unplaced(test_client: TestClient):
    """Once a movie is already ranked, the next one lands unplaced pending a duel."""
    first_id = _make_movie(test_client)
    second_id = _make_movie(test_client, imdb='tt7996', title='Also Ranked')
    user_headers = {'Authorization': f"Bearer {test_client.first_user.token}"}

    test_client.post(
        f"/v1/users/me/movies/{first_id}",
        headers=user_headers,
        json={'on_rankings': True},
    )
    response = test_client.post(
        f"/v1/users/me/movies/{second_id}",
        headers=user_headers,
        json={'on_rankings': True},
    )
    assert response.status_code == 201
    data = response.json()
    assert data['on_rankings'] is True
    assert data['rank'] is None


def test_set_movie_rank_inserts_and_shifts(test_client: TestClient):
    """Placing a movie at position N shifts existing movies at/after N down."""
    user_headers = {'Authorization': f"Bearer {test_client.first_user.token}"}
    ids = []
    for i in range(3):
        mid = _make_movie(test_client, imdb=f"tt700{i}", title=f"Ranked {i}")
        test_client.post(
            f"/v1/users/me/movies/{mid}",
            headers=user_headers,
            json={'on_rankings': True},
        )
        ids.append(mid)
    # Establish an initial 1..3 order.
    test_client.put(
        '/v1/users/me/movies/rankings/order',
        headers=user_headers,
        json={'movie_ids': ids},
    )

    # A fresh movie, added to rankings (unplaced), then placed at position 2.
    new_id = _make_movie(test_client, imdb='tt7999', title='Inserted')
    test_client.post(
        f"/v1/users/me/movies/{new_id}",
        headers=user_headers,
        json={'on_rankings': True},
    )
    resp = test_client.put(
        f"/v1/users/me/movies/{new_id}/rank",
        headers=user_headers,
        json={'position': 2},
    )
    assert resp.status_code == 200
    assert resp.json()['rank'] == 2

    listing = test_client.get('/v1/users/me/movies', headers=user_headers).json()
    ranked = sorted(
        [m for m in listing if m['rank'] is not None], key=lambda m: m['rank']
    )
    order = [(m['rank'], m['movie']['id']) for m in ranked]
    # ids[0]=1, inserted=2, ids[1]=3, ids[2]=4
    assert order == [(1, ids[0]), (2, new_id), (3, ids[1]), (4, ids[2])]


def test_lists_are_exclusive(test_client: TestClient):
    """One-home rule (#145): joining Rankings leaves the Watchlist."""
    movie_id = _make_movie(test_client)
    headers = {'Authorization': f"Bearer {test_client.first_user.token}"}

    # Add to watchlist only.
    r = test_client.post(
        f"/v1/users/me/movies/{movie_id}",
        headers=headers,
        json={'on_watchlist': True},
    )
    assert r.json()['on_watchlist'] is True
    assert r.json()['on_rankings'] is False

    # Promote to rankings -> leaves the watchlist. It's the only ranked movie,
    # so it auto-places at #1 (#289).
    r = test_client.post(
        f"/v1/users/me/movies/{movie_id}",
        headers=headers,
        json={'on_rankings': True},
    )
    assert r.json()['on_rankings'] is True
    assert r.json()['on_watchlist'] is False
    assert r.json()['rank'] == 1

    # Leave rankings -> on neither list, so the tracker is dropped entirely.
    test_client.put(
        f"/v1/users/me/movies/{movie_id}",
        headers=headers,
        json={'on_rankings': False},
    )
    listing = test_client.get('/v1/users/me/movies', headers=headers).json()
    assert all(t['movie']['id'] != movie_id for t in listing)


def test_reentering_rankings_starts_unplaced(test_client: TestClient):
    """Re-adding a movie to Rankings ignores any leftover rank (starts unplaced)."""
    other_id = _make_movie(test_client, imdb='tt7995', title='Stays Ranked')
    movie_id = _make_movie(test_client)
    h = {'Authorization': f"Bearer {test_client.first_user.token}"}
    # rank `other` first so the list isn't empty when `movie_id` re-enters below.
    test_client.post(
        f"/v1/users/me/movies/{other_id}", headers=h, json={'on_rankings': True}
    )
    # place it at #2
    test_client.post(
        f"/v1/users/me/movies/{movie_id}", headers=h, json={'on_rankings': True}
    )
    test_client.put(
        f"/v1/users/me/movies/{movie_id}/rank", headers=h, json={'position': 2}
    )
    # remove from rankings, then re-add -> must be unplaced again, not at its old rank
    test_client.put(
        f"/v1/users/me/movies/{movie_id}",
        headers=h,
        json={'on_watchlist': True, 'on_rankings': False},
    )
    r = test_client.post(
        f"/v1/users/me/movies/{movie_id}", headers=h, json={'on_rankings': True}
    )
    assert r.json()['on_rankings'] is True
    assert r.json()['rank'] is None


def test_reorder_rankings(test_client: TestClient):
    user_headers = {'Authorization': f"Bearer {test_client.first_user.token}"}
    ids = []
    for i in range(3):
        mid = _make_movie(test_client, imdb=f"tt900{i}", title=f"Movie {i}")
        test_client.post(
            f"/v1/users/me/movies/{mid}",
            headers=user_headers,
            json={'on_rankings': True},
        )
        ids.append(mid)

    # Reverse the order.
    reordered = list(reversed(ids))
    resp = test_client.put(
        '/v1/users/me/movies/rankings/order',
        headers=user_headers,
        json={'movie_ids': reordered},
    )
    assert resp.status_code == 200
    data = resp.json()
    ordered_ids = [m['movie']['id'] for m in data]
    assert ordered_ids == reordered
    assert [m['rank'] for m in data] == [1, 2, 3]


def test_get_user_movies(test_client: TestClient):
    movie_id = _make_movie(test_client)
    user_headers = {'Authorization': f"Bearer {test_client.first_user.token}"}
    test_client.post(
        f"/v1/users/me/movies/{movie_id}",
        headers=user_headers,
        json={'on_rankings': True},
    )

    response = test_client.get('/v1/users/me/movies', headers=user_headers)
    assert response.status_code == 200
    data = response.json()
    assert len(data) > 0
    assert data[0]['on_rankings'] is True


@patch('app.router.v1.router_movies.get_movie_detail')
def test_get_movie_enriches_on_view(mock_detail, test_client: TestClient):
    movie_id = _make_movie(test_client)
    mock_detail.return_value = {
        'director': 'Christopher Nolan',
        'actors': 'Leonardo DiCaprio',
        'genre': 'Sci-Fi',
        'plot': 'A thief who steals corporate secrets.',
        'year': 2010,
    }
    user_headers = {'Authorization': f"Bearer {test_client.first_user.token}"}
    resp = test_client.get(f"/v1/movies/{movie_id}", headers=user_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data['director'] == 'Christopher Nolan'
    assert data['genre'] == 'Sci-Fi'
    assert data['year'] == 2010


def test_create_movie_unauthenticated(test_client: TestClient):
    response = test_client.post(
        '/v1/movies', json={'title': 'Inception', 'imdb': 'tt1375666'}
    )
    assert response.status_code == 401


def test_create_movie_allowed_for_any_user(test_client: TestClient):
    """Regular users add to the shared catalog via the add-from-search flow."""
    user_token = test_client.first_user.token
    headers = {'Authorization': f"Bearer {user_token}"}
    response = test_client.post(
        '/v1/movies', headers=headers, json={'title': 'Inception', 'imdb': 'tt1375666'}
    )
    assert response.status_code == 201


def test_update_movie_requires_admin(test_client: TestClient):
    admin_token = test_client.admin_user.token
    admin_headers = {'Authorization': f"Bearer {admin_token}"}
    created = test_client.post(
        '/v1/movies',
        headers=admin_headers,
        json={'title': 'Inception', 'imdb': 'tt1375666'},
    )
    movie_id = created.json()['id']

    user_token = test_client.first_user.token
    user_headers = {'Authorization': f"Bearer {user_token}"}
    response = test_client.put(
        f"/v1/movies/{movie_id}", headers=user_headers, json={'title': 'Hacked'}
    )
    assert response.status_code == 403


def test_search_movies_requires_auth(test_client: TestClient):
    response = test_client.get('/v1/movies/search?q=matrix')
    assert response.status_code == 401


@patch('app.services.tmdb.get_settings')
def test_search_movies_not_configured(mock_settings, test_client: TestClient):
    mock_settings.return_value = Settings(tmdb_api_key=None, env='github')
    user_headers = {'Authorization': f"Bearer {test_client.first_user.token}"}
    response = test_client.get('/v1/movies/search?q=matrix', headers=user_headers)
    assert response.status_code == 503


@patch('app.services.tmdb.get_settings')
@patch('app.services.tmdb.requests.get')
def test_search_movies_returns_results(
    mock_get, mock_settings, test_client: TestClient
):
    mock_settings.return_value = Settings(tmdb_api_key='test-key', env='github')
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.headers = {}
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = {
        'results': [
            {
                'id': 603,
                'title': 'The Matrix',
                'release_date': '1999-03-30',
                'poster_path': '/matrix.jpg',
                'popularity': 84.1,
            },
            {
                'id': 604,
                'title': 'The Matrix Reloaded',
                'release_date': '2003-05-15',
                'poster_path': None,
            },
        ],
    }
    mock_get.return_value = mock_response

    user_headers = {'Authorization': f"Bearer {test_client.first_user.token}"}
    response = test_client.get('/v1/movies/search?q=matrix', headers=user_headers)
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert data[0]['tmdb'] == 603
    assert data[0]['title'] == 'The Matrix'
    assert data[0]['poster_url'] == 'https://image.tmdb.org/t/p/w500/matrix.jpg'
    # TMDB title search carries no IMDb id.
    assert data[0]['imdb'] is None
    # Full release_date, not just year, so the frontend can show unreleased
    # titles a date instead of a rank affordance (web#180).
    assert data[0]['release_date'] == '1999-03-30'
    # A missing poster_path becomes null rather than a URL that would 404.
    assert data[1]['poster_url'] is None


@patch('app.router.v1.router_movies.tmdb_search_movies')
def test_search_retries_with_spelling_fix(mock_search, test_client: TestClient):
    """An empty result retries once with a spell-corrected query."""
    hit = [{'tmdb': 329, 'title': 'Jurassic Park', 'year': '1993'}]
    mock_search.side_effect = [[], hit]
    headers = {'Authorization': f"Bearer {test_client.first_user.token}"}

    response = test_client.get('/v1/movies/search?q=jurrasic', headers=headers)
    assert response.status_code == 200
    assert response.json()[0]['title'] == 'Jurassic Park'
    assert mock_search.call_count == 2
    assert mock_search.call_args_list[1].args[0] == 'jurassic'


@patch('app.router.v1.router_movies.tmdb_search_movies')
def test_search_no_retry_when_spelling_correct(mock_search, test_client: TestClient):
    mock_search.return_value = []
    headers = {'Authorization': f"Bearer {test_client.first_user.token}"}

    response = test_client.get('/v1/movies/search?q=jurassic', headers=headers)
    assert response.status_code == 200
    assert response.json() == []
    assert mock_search.call_count == 1


@patch('app.router.v1.router_movies.tmdb_search_movies')
def test_search_badges_already_ranked_result(mock_search, test_client: TestClient):
    """web#31: a search hit matching a movie the user has ranked carries
    on_rankings + rank; an untracked hit in the same response stays false/None.
    Since #163 the join is on tmdb, not imdb."""
    admin_headers = {'Authorization': f"Bearer {test_client.admin_user.token}"}
    create_resp = test_client.post(
        '/v1/movies',
        headers=admin_headers,
        json={'title': 'Inception', 'tmdb': 27205, 'imdb': 'tt1375666'},
    )
    movie_id = create_resp.json()['id']

    user_headers = {'Authorization': f"Bearer {test_client.first_user.token}"}
    mark_resp = test_client.post(
        f"/v1/users/me/movies/{movie_id}",
        headers=user_headers,
        json={'on_rankings': True},
    )
    assert mark_resp.status_code == 201
    rank_resp = test_client.put(
        f"/v1/users/me/movies/{movie_id}/rank",
        headers=user_headers,
        json={'position': 1},
    )
    assert rank_resp.status_code == 200

    # Title-search hits carry no imdb — the badge join has to work off tmdb alone.
    mock_search.return_value = [
        {'tmdb': 27205, 'title': 'Inception', 'year': '2010'},
        {'tmdb': 329, 'title': 'Jurassic Park', 'year': '1993'},
    ]
    response = test_client.get('/v1/movies/search?q=inception', headers=user_headers)
    assert response.status_code == 200
    data = {r['tmdb']: r for r in response.json()}
    assert data[27205]['on_rankings'] is True
    assert data[27205]['rank'] == 1
    assert data[329]['on_rankings'] is False
    assert data[329]['on_watchlist'] is False
    assert data[329]['rank'] is None


# --- Watch providers (web#26) ---
PROVIDER_PAYLOAD = {
    'results': {
        'US': {
            'link': 'https://www.themoviedb.org/movie/603/watch?locale=US',
            'flatrate': [
                {
                    'provider_id': 8,
                    'provider_name': 'Netflix',
                    'logo_path': '/netflix.jpg',
                    'display_priority': 0,
                }
            ],
            'rent': [
                {
                    'provider_id': 2,
                    'provider_name': 'Apple TV',
                    'logo_path': '/apple.jpg',
                    'display_priority': 0,
                }
            ],
        }
    }
}


@patch('app.services.tmdb.try_request')
def test_movie_watch_providers(mock_request, test_client: TestClient):
    watch_providers.reset_cache()
    mock_request.return_value = PROVIDER_PAYLOAD
    headers = {'Authorization': f"Bearer {test_client.admin_user.token}"}
    movie_id = test_client.post(
        '/v1/movies', headers=headers, json={'title': 'The Matrix', 'tmdb': 603}
    ).json()['id']

    response = test_client.get(
        f"/v1/movies/{movie_id}/watch-providers", headers=headers
    )

    assert response.status_code == 200
    data = response.json()
    assert data['region'] == 'US'
    assert data['attribution'] == 'JustWatch'
    assert data['stream'] == [
        {
            'provider_id': 8,
            'name': 'Netflix',
            'logo_url': 'https://image.tmdb.org/t/p/w92/netflix.jpg',
        }
    ]
    assert [p['name'] for p in data['rent']] == ['Apple TV']
    assert data['free'] == []


@patch('app.services.tmdb.try_request')
def test_movie_watch_providers_accepts_a_region(mock_request, test_client: TestClient):
    watch_providers.reset_cache()
    mock_request.return_value = PROVIDER_PAYLOAD
    headers = {'Authorization': f"Bearer {test_client.admin_user.token}"}
    movie_id = test_client.post(
        '/v1/movies', headers=headers, json={'title': 'The Matrix', 'tmdb': 603}
    ).json()['id']

    response = test_client.get(
        f"/v1/movies/{movie_id}/watch-providers?region=GB", headers=headers
    )

    assert response.status_code == 200
    # TMDB has no GB block in this payload — empty, not an error.
    assert response.json() == {
        'region': 'GB',
        'link': None,
        'attribution': 'JustWatch',
        'stream': [],
        'free': [],
        'rent': [],
        'buy': [],
    }


def test_movie_watch_providers_unknown_movie_404s(test_client: TestClient):
    headers = {'Authorization': f"Bearer {test_client.admin_user.token}"}
    response = test_client.get('/v1/movies/nope/watch-providers', headers=headers)
    assert response.status_code == 404


def test_movie_watch_providers_requires_auth(test_client: TestClient):
    headers = {'Authorization': f"Bearer {test_client.admin_user.token}"}
    movie_id = test_client.post(
        '/v1/movies', headers=headers, json={'title': 'The Matrix', 'tmdb': 603}
    ).json()['id']

    response = test_client.get(f"/v1/movies/{movie_id}/watch-providers")
    assert response.status_code == 401
