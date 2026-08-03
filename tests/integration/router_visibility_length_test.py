# pylint: disable=missing-function-docstring
"""
Viewer-controlled shelf length (#279).

Length is opt-in and scoped to one shelf at a time: the multi-shelf hub view
always gets the small preview size, and `limit`/`offset` only take effect
once a caller names which `shelf` they want deep.
"""

from fastapi.testclient import TestClient

HANDLE = 'avery'


def _auth(token: str) -> dict:
    return {'Authorization': f"Bearer {token}"}


def _rank_movies(test_client: TestClient, token: str, count: int) -> None:
    admin = _auth(test_client.admin_user.token)
    for i in range(count):
        movie_id = test_client.post(
            '/v1/movies',
            headers=admin,
            json={'title': f"Movie {i}", 'imdb': f"tt{i:07d}"},
        ).json()['id']
        test_client.post(
            f"/v1/users/me/movies/{movie_id}",
            headers=_auth(token),
            json={'on_rankings': True},
        )
        test_client.put(
            f"/v1/users/me/movies/{movie_id}/rank",
            headers=_auth(token),
            json={'position': 1},
        )


def _watchlist_movies(
    test_client: TestClient, token: str, count: int, start=1000
) -> None:
    admin = _auth(test_client.admin_user.token)
    for i in range(start, start + count):
        movie_id = test_client.post(
            '/v1/movies',
            headers=admin,
            json={'title': f"Watchlist {i}", 'imdb': f"tt{i:07d}"},
        ).json()['id']
        test_client.post(
            f"/v1/users/me/movies/{movie_id}",
            headers=_auth(token),
            json={'on_watchlist': True},
        )


def _share_movies_publicly(
    test_client: TestClient, token: str, watchlist=False
) -> None:
    body = {
        'handle': HANDLE,
        'visibility_profile': 'public',
        'visibility_movies': 'public',
    }
    if watchlist:
        body['visibility_watchlist_movies'] = 'public'
    test_client.put('/v1/users/me/visibility', headers=_auth(token), json=body)


def test_default_length_is_unchanged(test_client: TestClient):
    token = test_client.first_user.token
    _rank_movies(test_client, token, 30)
    _share_movies_publicly(test_client, token)

    shelf = test_client.get(f"/v1/public/{HANDLE}").json()['shelves'][0]
    assert len(shelf['items']) == 25
    assert shelf['ranked_count'] == 30


def test_limit_only_applies_to_a_named_shelf(test_client: TestClient):
    token = test_client.first_user.token
    _rank_movies(test_client, token, 30)
    _share_movies_publicly(test_client, token)

    # No `shelf` named: limit/offset are ignored, default preview holds.
    shelf = test_client.get(f"/v1/public/{HANDLE}", params={'limit': 5}).json()[
        'shelves'
    ][0]
    assert len(shelf['items']) == 25

    # `shelf` named: limit takes effect, and only for that shelf.
    shelf = test_client.get(
        f"/v1/public/{HANDLE}", params={'shelf': 'movies', 'limit': 5}
    ).json()['shelves'][0]
    assert len(shelf['items']) == 5


def test_offset_pages_through_a_long_shelf(test_client: TestClient):
    token = test_client.first_user.token
    _rank_movies(test_client, token, 30)
    _share_movies_publicly(test_client, token)

    page1 = test_client.get(
        f"/v1/public/{HANDLE}", params={'shelf': 'movies', 'limit': 10, 'offset': 0}
    ).json()['shelves'][0]['items']
    page2 = test_client.get(
        f"/v1/public/{HANDLE}", params={'shelf': 'movies', 'limit': 10, 'offset': 10}
    ).json()['shelves'][0]['items']
    assert len(page1) == len(page2) == 10
    assert {item['rank'] for item in page1}.isdisjoint({item['rank'] for item in page2})


def test_limit_beyond_max_clamps_rather_than_errors(test_client: TestClient):
    token = test_client.first_user.token
    _rank_movies(test_client, token, 5)
    _share_movies_publicly(test_client, token)

    response = test_client.get(
        f"/v1/public/{HANDLE}", params={'shelf': 'movies', 'limit': 999_999}
    )
    assert response.status_code == 200
    assert len(response.json()['shelves'][0]['items']) == 5


def test_ranked_count_is_independent_of_limit(test_client: TestClient):
    token = test_client.first_user.token
    _rank_movies(test_client, token, 30)
    _share_movies_publicly(test_client, token)

    shelf = test_client.get(
        f"/v1/public/{HANDLE}", params={'shelf': 'movies', 'limit': 3}
    ).json()['shelves'][0]
    assert shelf['ranked_count'] == 30
    assert len(shelf['items']) == 3


def test_watchlist_length_is_parameterized_the_same_way(test_client: TestClient):
    token = test_client.first_user.token
    _rank_movies(test_client, token, 2)
    _watchlist_movies(test_client, token, 30)
    _share_movies_publicly(test_client, token, watchlist=True)

    shelf = test_client.get(f"/v1/public/{HANDLE}").json()['shelves'][0]
    assert shelf['watchlist_count'] == 30
    assert len(shelf['watchlist']) == 25

    deep = test_client.get(
        f"/v1/public/{HANDLE}",
        params={'shelf': 'movies', 'kind': 'watchlist', 'limit': 5},
    ).json()['shelves'][0]
    assert deep['watchlist_count'] == 30
    assert len(deep['watchlist']) == 5
    # Requesting watchlist depth doesn't also deepen the ranked list.
    assert len(deep['items']) == 2


def test_unknown_named_shelf_404s_like_any_other_invisible_one(test_client: TestClient):
    token = test_client.first_user.token
    _rank_movies(test_client, token, 3)
    _share_movies_publicly(test_client, token)

    unknown_category = test_client.get(
        f"/v1/public/{HANDLE}", params={'shelf': 'countries'}
    )
    not_visible = test_client.get(f"/v1/public/{HANDLE}", params={'shelf': 'tv'})
    assert unknown_category.status_code == not_visible.status_code == 404
    assert unknown_category.json() == not_visible.json()
