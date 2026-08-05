# pylint: disable=missing-function-docstring
"""Deletion consistency for every public shelf and mutable read (#297)."""

import pytest
from fastapi.testclient import TestClient

DOMAINS = (
    (
        'movies',
        '/v1/movies',
        {'title': 'Heat', 'imdb': 'tt0113277'},
        'visibility_movies',
    ),
    (
        'tv-shows',
        '/v1/tv-shows',
        {'title': 'Severance', 'tvmaze': 44932},
        'visibility_tv',
    ),
    (
        'books',
        '/v1/books',
        {'title': 'Piranesi', 'isbn': '9781635575637'},
        'visibility_books',
    ),
    (
        'games',
        '/v1/games',
        {'title': 'Hades', 'igdb': 113112},
        'visibility_games',
    ),
)


def _auth(token: str) -> dict:
    return {'Authorization': f'Bearer {token}'}


@pytest.mark.parametrize(
    ('tracker_path', 'catalog_path', 'catalog_payload', 'visibility_field'), DOMAINS
)
def test_delete_is_immediately_visible_everywhere(
    test_client: TestClient,
    tracker_path: str,
    catalog_path: str,
    catalog_payload: dict,
    visibility_field: str,
):
    """A deletion cannot survive in public, rankings, or mutable-read caches."""
    user_headers = _auth(test_client.first_user.token)
    item_id = test_client.post(
        catalog_path,
        headers=_auth(test_client.admin_user.token),
        json=catalog_payload,
    ).json()['id']
    assert (
        test_client.post(
            f'/v1/users/me/{tracker_path}/{item_id}',
            headers=user_headers,
            json={'on_rankings': True},
        ).status_code
        == 201
    )
    test_client.put(
        '/v1/users/me/visibility',
        headers=user_headers,
        json={
            'handle': 'avery',
            'visibility_profile': 'public',
            visibility_field: 'public',
        },
    )

    public_before = test_client.get('/v1/public/avery')
    assert public_before.json()['total_ranked'] == 1
    assert public_before.headers['cache-control'] == 'private, no-store'

    rankings_before = test_client.get(
        f'/v1/users/me/{tracker_path}?on_rankings=true', headers=user_headers
    )
    assert len(rankings_before.json()) == 1
    assert rankings_before.headers['cache-control'] == 'private, no-store'

    assert (
        test_client.delete(
            f'/v1/users/me/{tracker_path}/{item_id}', headers=user_headers
        ).status_code
        == 204
    )

    public_after = test_client.get('/v1/public/avery')
    assert public_after.json()['total_ranked'] == 0
    assert public_after.json()['shelves'][0]['items'] == []
    assert public_after.headers['cache-control'] == 'private, no-store'

    rankings_after = test_client.get(
        f'/v1/users/me/{tracker_path}?on_rankings=true', headers=user_headers
    )
    assert rankings_after.json() == []
    assert rankings_after.headers['cache-control'] == 'private, no-store'


def test_search_responses_are_not_cacheable(test_client: TestClient, monkeypatch):
    empty_results = {'movies': [], 'tv_shows': [], 'books': [], 'games': []}
    monkeypatch.setattr(
        'app.router.v1.router_search._fan_out', lambda _q: empty_results
    )
    response = test_client.get(
        '/v1/search?q=anything', headers=_auth(test_client.first_user.token)
    )
    assert response.status_code == 200
    assert response.headers['cache-control'] == 'private, no-store'
