# pylint: disable=missing-module-docstring, missing-function-docstring
import pytest
from fastapi.testclient import TestClient

from app.services.tracker_query import MAX_PAGE

# (list path, catalog path, catalog payload builder)
DOMAINS = (
    ('movies', '/v1/movies', lambda i: {'title': f'M{i}', 'imdb': f'tt50{i}'}),
    (
        'tv-shows',
        '/v1/tv-shows',
        lambda i: {'title': f'T{i}', 'tvmaze': 5000 + i},
    ),
    ('books', '/v1/books', lambda i: {'title': f'B{i}', 'googleid': f'gid50{i}'}),
    ('games', '/v1/games', lambda i: {'title': f'G{i}', 'igdb': 5000 + i}),
)


def _auth(token: str) -> dict:
    return {'Authorization': f'Bearer {token}'}


def _seed(test_client: TestClient, domain: str, catalog_path: str, payload, count: int):
    """Create ``count`` catalog rows; rank the odd ones, queue the even ones."""
    token = test_client.first_user.token
    entity_ids = []
    for i in range(count):
        entity_id = test_client.post(
            catalog_path, headers=_auth(test_client.admin_user.token), json=payload(i)
        ).json()['id']
        entity_ids.append(entity_id)
        test_client.post(
            f'/v1/users/me/{domain}/{entity_id}',
            headers=_auth(token),
            json=({'on_rankings': True} if i % 2 else {'on_watchlist': True}),
        )
    return entity_ids


@pytest.mark.parametrize('domain,catalog_path,payload', DOMAINS)
def test_limit_and_offset_page_the_list(
    test_client: TestClient, domain, catalog_path, payload
):
    token = test_client.first_user.token
    _seed(test_client, domain, catalog_path, payload, 6)
    path = f'/v1/users/me/{domain}'

    assert len(test_client.get(path, headers=_auth(token)).json()) == 6
    assert len(test_client.get(f'{path}?limit=2', headers=_auth(token)).json()) == 2
    assert (
        len(test_client.get(f'{path}?limit=10&offset=4', headers=_auth(token)).json())
        == 2
    )


@pytest.mark.parametrize('domain,catalog_path,payload', DOMAINS)
def test_include_total_returns_page_metadata(
    test_client: TestClient, domain, catalog_path, payload
):
    token = test_client.first_user.token
    _seed(test_client, domain, catalog_path, payload, 6)
    path = f'/v1/users/me/{domain}'

    page = test_client.get(
        f'{path}?on_rankings=true&limit=2&offset=2&include_total=true',
        headers=_auth(token),
    ).json()

    assert page['total'] == 3
    assert page['limit'] == 2
    assert page['offset'] == 2
    assert len(page['items']) == 1
    assert page['items'][0]['on_rankings'] is True


@pytest.mark.parametrize('domain,catalog_path,payload', DOMAINS)
def test_list_filters(test_client: TestClient, domain, catalog_path, payload):
    token = test_client.first_user.token
    _seed(test_client, domain, catalog_path, payload, 6)
    path = f'/v1/users/me/{domain}'

    ranked = test_client.get(f'{path}?on_rankings=true', headers=_auth(token)).json()
    queued = test_client.get(f'{path}?on_watchlist=true', headers=_auth(token)).json()
    assert len(ranked) == 3
    assert len(queued) == 3
    assert all(r['on_rankings'] for r in ranked)
    assert all(q['on_watchlist'] for q in queued)


@pytest.mark.parametrize('domain,catalog_path,payload', DOMAINS)
def test_list_searches_titles_case_insensitively(
    test_client: TestClient, domain, catalog_path, payload
):
    token = test_client.first_user.token
    _seed(test_client, domain, catalog_path, payload, 3)
    path = f'/v1/users/me/{domain}'

    result = test_client.get(
        f'{path}?search={domain[0].lower()}1&include_total=true',
        headers=_auth(token),
    ).json()

    assert result['total'] == 1
    assert (
        result['items'][0][
            {
                'movies': 'movie',
                'tv-shows': 'tv_show',
                'books': 'book',
                'games': 'game',
            }[domain]
        ]['title']
        == f'{domain[0].upper()}1'
    )


@pytest.mark.parametrize('domain,catalog_path,payload', DOMAINS)
@pytest.mark.parametrize('sort', ('rank', '-rank'))
def test_list_sorts_rank_and_keeps_nulls_last(
    test_client: TestClient, domain, catalog_path, payload, sort
):
    token = test_client.first_user.token
    entity_ids = _seed(test_client, domain, catalog_path, payload, 6)
    path = f'/v1/users/me/{domain}'
    for position, entity_id in enumerate(entity_ids[1::2], start=1):
        response = test_client.put(
            f'{path}/{entity_id}/rank',
            headers=_auth(token),
            json={'position': position},
        )
        assert response.status_code == 200

    result = test_client.get(
        f'{path}?sort={sort}&include_total=true', headers=_auth(token)
    ).json()
    ranks = [item['rank'] for item in result['items']]

    assert ranks[:3] == ([1, 2, 3] if sort == 'rank' else [3, 2, 1])
    assert ranks[3:] == [None, None, None]


@pytest.mark.parametrize('domain,catalog_path,payload', DOMAINS)
@pytest.mark.parametrize('sort', ('title', '-completed_at'))
def test_list_sorts_title_and_completed_at(
    test_client: TestClient, domain, catalog_path, payload, sort
):
    token = test_client.first_user.token
    _seed(test_client, domain, catalog_path, payload, 6)
    path = f'/v1/users/me/{domain}'

    items = test_client.get(
        f'{path}?sort={sort}&include_total=true', headers=_auth(token)
    ).json()['items']

    if sort == 'title':
        title_key = {
            'movies': 'movie',
            'tv-shows': 'tv_show',
            'books': 'book',
            'games': 'game',
        }[domain]
        assert [item[title_key]['title'] for item in items] == [
            f'{domain[0].upper()}{i}' for i in range(6)
        ]
    else:
        assert all(item['completed_at'] is not None for item in items[:3])
        assert all(item['completed_at'] is None for item in items[3:])


def test_limit_is_capped_at_max_page(test_client: TestClient):
    response = test_client.get(
        f'/v1/users/me/movies?limit={MAX_PAGE + 1}',
        headers=_auth(test_client.first_user.token),
    )
    assert response.status_code == 422


def test_list_rejects_an_unknown_sort(test_client: TestClient):
    response = test_client.get(
        '/v1/users/me/movies?sort=created_at',
        headers=_auth(test_client.first_user.token),
    )
    assert response.status_code == 422
