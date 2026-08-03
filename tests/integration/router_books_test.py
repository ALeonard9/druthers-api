# pylint: disable=missing-module-docstring, missing-function-docstring
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient


def _make_book(test_client: TestClient, title='Dune', **extra) -> str:
    headers = {'Authorization': f"Bearer {test_client.admin_user.token}"}
    resp = test_client.post(
        '/v1/books', headers=headers, json={'title': title, **extra}
    )
    assert resp.status_code == 201
    return resp.json()['id']


# --- Global catalog ---
def test_create_book(test_client: TestClient):
    headers = {'Authorization': f"Bearer {test_client.admin_user.token}"}
    response = test_client.post(
        '/v1/books', headers=headers, json={'title': 'Dune', 'isbn': '9780441172719'}
    )
    assert response.status_code == 201
    data = response.json()
    assert data['title'] == 'Dune'
    assert data['isbn'] == '9780441172719'


def test_get_books(test_client: TestClient):
    _make_book(test_client)
    response = test_client.get('/v1/books')
    assert response.status_code == 200
    assert len(response.json()) > 0


def test_create_book_unauthenticated(test_client: TestClient):
    response = test_client.post('/v1/books', json={'title': 'Dune'})
    assert response.status_code == 401


def test_create_book_allowed_for_any_user(test_client: TestClient):
    """Regular users add to the shared catalog via the add-from-search flow."""
    headers = {'Authorization': f"Bearer {test_client.first_user.token}"}
    response = test_client.post('/v1/books', headers=headers, json={'title': 'Dune'})
    assert response.status_code == 201


def test_create_duplicate_isbn_rejected(test_client: TestClient):
    _make_book(test_client, isbn='9780441172719')
    headers = {'Authorization': f"Bearer {test_client.admin_user.token}"}
    dup = test_client.post(
        '/v1/books',
        headers=headers,
        json={'title': 'Dune again', 'isbn': '9780441172719'},
    )
    assert dup.status_code == 400


@patch('app.router.v1.router_books.get_book_detail')
def test_get_book_enriches_on_view(mock_detail, test_client: TestClient):
    book_id = _make_book(test_client)
    mock_detail.return_value = {
        'authors': 'Frank Herbert',
        'year': 1965,
        'genre': 'Fiction',
        'description': 'A desert planet and a great destiny.',
        'page_count': 412,
        'rating': 4.5,
    }
    headers = {'Authorization': f"Bearer {test_client.first_user.token}"}
    resp = test_client.get(f"/v1/books/{book_id}", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data['authors'] == 'Frank Herbert'
    assert data['year'] == 1965
    assert data['description'] == 'A desert planet and a great destiny.'


# --- Search proxy ---
def test_search_books_requires_auth(test_client: TestClient):
    response = test_client.get('/v1/books/search?q=dune')
    assert response.status_code == 401


@patch('app.services.book_search.requests.get')
def test_search_books_returns_results(mock_get, test_client: TestClient):
    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = {
        'docs': [
            {
                'key': '/works/OL893415W',
                'title': 'Dune',
                'author_name': ['Frank Herbert'],
                'first_publish_year': 1965,
                'isbn': ['0441172717', '9780441172719'],
                'cover_i': 11481354,
            }
        ]
    }
    mock_get.return_value = mock_response

    headers = {'Authorization': f"Bearer {test_client.first_user.token}"}
    response = test_client.get('/v1/books/search?q=dune', headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]['isbn'] == '9780441172719'  # ISBN-13 preferred
    assert data[0]['authors'] == 'Frank Herbert'
    assert data[0]['year'] == '1965'
    assert data[0]['poster_url'] == (
        'https://covers.openlibrary.org/b/id/11481354-L.jpg'
    )


# --- Trackers (Movies-parity lists) ---
def test_mark_first_book_to_rankings_auto_places_at_one(test_client: TestClient):
    """First book into an empty ranked list auto-places at #1 (#289)."""
    book_id = _make_book(test_client)
    headers = {'Authorization': f"Bearer {test_client.first_user.token}"}
    response = test_client.post(
        f"/v1/users/me/books/{book_id}",
        headers=headers,
        json={'on_rankings': True, 'notes': 'A masterpiece.'},
    )
    assert response.status_code == 201
    data = response.json()
    assert data['on_rankings'] is True
    assert data['on_watchlist'] is False
    assert data['rank'] == 1
    assert data['notes'] == 'A masterpiece.'


def test_mark_second_book_to_rankings_is_unplaced(test_client: TestClient):
    """Once a book is already ranked, the next one lands unplaced pending a duel."""
    first_id = _make_book(test_client)
    second_id = _make_book(test_client, title='Also Ranked')
    headers = {'Authorization': f"Bearer {test_client.first_user.token}"}

    test_client.post(
        f"/v1/users/me/books/{first_id}", headers=headers, json={'on_rankings': True}
    )
    response = test_client.post(
        f"/v1/users/me/books/{second_id}", headers=headers, json={'on_rankings': True}
    )
    assert response.status_code == 201
    data = response.json()
    assert data['on_rankings'] is True
    assert data['rank'] is None


def test_lists_are_exclusive(test_client: TestClient):
    """One-home rule (#145): joining Rankings leaves the Watchlist."""
    book_id = _make_book(test_client)
    headers = {'Authorization': f"Bearer {test_client.first_user.token}"}

    # Add to watchlist only.
    r = test_client.post(
        f"/v1/users/me/books/{book_id}",
        headers=headers,
        json={'on_watchlist': True},
    )
    assert r.json()['on_watchlist'] is True
    assert r.json()['on_rankings'] is False

    # Promote to rankings -> leaves the watchlist. It's the only ranked book,
    # so it auto-places at #1 (#289).
    r = test_client.post(
        f"/v1/users/me/books/{book_id}",
        headers=headers,
        json={'on_rankings': True},
    )
    assert r.json()['on_rankings'] is True
    assert r.json()['on_watchlist'] is False
    assert r.json()['rank'] == 1

    # Leave rankings -> on neither list, so the tracker is dropped entirely.
    test_client.put(
        f"/v1/users/me/books/{book_id}",
        headers=headers,
        json={'on_rankings': False},
    )
    listing = test_client.get('/v1/users/me/books', headers=headers).json()
    assert all(t['book']['id'] != book_id for t in listing)


def test_set_book_rank_inserts_and_shifts(test_client: TestClient):
    headers = {'Authorization': f"Bearer {test_client.first_user.token}"}
    ids = []
    for i in range(3):
        bid = _make_book(test_client, title=f"Ranked {i}")
        test_client.post(
            f"/v1/users/me/books/{bid}", headers=headers, json={'on_rankings': True}
        )
        ids.append(bid)
    test_client.put(
        '/v1/users/me/books/rankings/order',
        headers=headers,
        json={'book_ids': ids},
    )

    new_id = _make_book(test_client, title='Inserted')
    test_client.post(
        f"/v1/users/me/books/{new_id}", headers=headers, json={'on_rankings': True}
    )
    resp = test_client.put(
        f"/v1/users/me/books/{new_id}/rank", headers=headers, json={'position': 2}
    )
    assert resp.status_code == 200
    assert resp.json()['rank'] == 2

    listing = test_client.get('/v1/users/me/books', headers=headers).json()
    ranked = sorted(
        [t for t in listing if t['rank'] is not None], key=lambda t: t['rank']
    )
    order = [(t['rank'], t['book']['id']) for t in ranked]
    assert order == [(1, ids[0]), (2, new_id), (3, ids[1]), (4, ids[2])]


def test_reorder_rankings(test_client: TestClient):
    headers = {'Authorization': f"Bearer {test_client.first_user.token}"}
    ids = []
    for i in range(3):
        bid = _make_book(test_client, title=f"Book {i}")
        test_client.post(
            f"/v1/users/me/books/{bid}", headers=headers, json={'on_rankings': True}
        )
        ids.append(bid)

    reordered = list(reversed(ids))
    resp = test_client.put(
        '/v1/users/me/books/rankings/order',
        headers=headers,
        json={'book_ids': reordered},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert [t['book']['id'] for t in data] == reordered
    assert [t['rank'] for t in data] == [1, 2, 3]
