# pylint: disable=missing-module-docstring, missing-function-docstring
import io
from datetime import datetime, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient

HEADER = (
    'Title,Author,ISBN,ISBN13,My Rating,Number of Pages,Year Published,'
    'Original Publication Year,Exclusive Shelf,My Review,Date Read\n'
)

CSV = (
    HEADER
    + (
        'Dune,Frank Herbert,="0441172717",="9780441172719",5,412,1990,1965,'
        'read,A classic.,2020/01/02\n'
    )
    + 'Piranesi,Susanna Clarke,="",="9781635575637",0,245,2020,2020,to-read,,\n'
    + ',Nobody,="",="",0,,,,read,,\n'
)


def _auth(token: str) -> dict:
    return {'Authorization': f'Bearer {token}'}


def _upload(test_client: TestClient, token: str, content: str = CSV):
    return test_client.post(
        '/v1/users/me/import/goodreads',
        headers=_auth(token),
        files={'file': ('goodreads.csv', io.BytesIO(content.encode()), 'text/csv')},
    )


@patch('app.services.goodreads_import.get_book_detail', return_value=None)
def test_goodreads_import_creates_books_and_trackers(
    mock_detail, test_client: TestClient
):
    token = test_client.first_user.token
    response = _upload(test_client, token)
    assert response.status_code == 200
    body = response.json()
    assert body['books_created'] == 2
    assert body['trackers_created'] == 2
    assert body['unplaced_rankings_count'] == 0
    assert body['next_unplaced_book_id'] is None
    assert body['skipped'] == [{'row': 4, 'reason': 'Missing title'}]

    books = test_client.get('/v1/users/me/books', headers=_auth(token)).json()
    by_title = {b['book']['title']: b for b in books}
    dune = by_title['Dune']
    assert dune['on_rankings'] is True
    assert dune['on_watchlist'] is False
    assert dune['rank'] == 1
    assert dune['completed_at'] == '2020-01-02'
    assert dune['book']['isbn'] == '9780441172719'
    assert dune['book']['year'] == 1965
    assert 'A classic.' in dune['notes']
    assert 'Goodreads rating: 5/5' in dune['notes']
    piranesi = by_title['Piranesi']
    assert piranesi['on_watchlist'] is True
    assert piranesi['on_rankings'] is False
    assert mock_detail.call_count == 2


@patch('app.services.goodreads_import.get_book_detail')
def test_goodreads_import_enriches_new_catalog_books(
    mock_detail, test_client: TestClient
):
    mock_detail.return_value = {
        'title': 'Dune',
        'isbn': '9780441172719',
        'authors': 'Frank Herbert',
        'year': 1965,
        'genre': 'Science fiction',
        'description': 'A desert planet and a great destiny.',
        'page_count': 604,
        'rating': 4.2,
        'language': 'eng',
        'poster_url': 'https://covers.openlibrary.org/b/id/123-L.jpg',
    }
    response = _upload(test_client, test_client.first_user.token)
    assert response.status_code == 200

    books = test_client.get(
        '/v1/users/me/books', headers=_auth(test_client.first_user.token)
    ).json()
    dune = next(book for book in books if book['book']['title'] == 'Dune')['book']
    assert dune['poster_url'] == 'https://covers.openlibrary.org/b/id/123-L.jpg'
    dune_id = dune['id']
    detail = test_client.get(
        f'/v1/books/{dune_id}', headers=_auth(test_client.first_user.token)
    ).json()
    assert detail['genre'] == 'Science fiction'
    assert detail['description'] == 'A desert planet and a great destiny.'
    assert detail['page_count'] == 604


@patch('app.services.goodreads_import.get_book_detail', return_value=None)
def test_goodreads_import_keeps_csv_fields_when_provider_misses(
    mock_detail, test_client: TestClient
):
    _upload(test_client, test_client.first_user.token)
    books = test_client.get(
        '/v1/users/me/books', headers=_auth(test_client.first_user.token)
    ).json()
    dune = next(book for book in books if book['book']['title'] == 'Dune')['book']
    assert dune['authors'] == 'Frank Herbert'
    assert dune['isbn'] == '9780441172719'
    assert dune['year'] == 1965
    assert dune['poster_url'] is None
    assert mock_detail.call_count == 2


@patch('app.services.goodreads_import.get_book_detail', return_value=None)
def test_goodreads_import_is_idempotent(mock_detail, test_client: TestClient):
    token = test_client.first_user.token
    _upload(test_client, token)
    body = _upload(test_client, token).json()
    assert body['books_created'] == 0
    assert body['books_matched'] == 2
    assert body['trackers_created'] == 0
    assert body['trackers_updated'] == 0
    books = test_client.get('/v1/users/me/books', headers=_auth(token)).json()
    assert len(books) == 2
    dune = next(book for book in books if book['book']['title'] == 'Dune')
    assert dune['rank'] == 1
    assert dune['notes'] == 'A classic.\n\nGoodreads rating: 5/5'
    assert mock_detail.call_count == 2


@patch('app.services.goodreads_import.get_book_detail', return_value=None)
def test_goodreads_import_promotes_but_never_demotes(
    _mock_detail, test_client: TestClient
):
    token = test_client.first_user.token
    _upload(test_client, token)
    # Same file with Piranesi now read: watchlist → rankings
    promoted = CSV.replace('to-read', 'read')
    body = _upload(test_client, token, promoted).json()
    assert body['trackers_updated'] == 1
    books = test_client.get('/v1/users/me/books', headers=_auth(token)).json()
    piranesi = next(b for b in books if b['book']['title'] == 'Piranesi')
    assert piranesi['on_rankings'] is True
    assert piranesi['on_watchlist'] is False
    assert piranesi['rank'] is None


@patch('app.services.goodreads_import.get_book_detail', return_value=None)
def test_goodreads_import_leaves_all_read_rows_after_the_first_unplaced(
    _mock_detail, test_client: TestClient
):
    content = (
        HEADER
        + 'First,Author,="",="9780000000001",0,100,2020,2020,read,,\n'
        + 'Second,Author,="",="9780000000002",0,100,2020,2020,read,,\n'
        + 'Third,Author,="",="9780000000003",0,100,2020,2020,read,,\n'
    )
    body = _upload(test_client, test_client.first_user.token, content).json()
    books = test_client.get(
        '/v1/users/me/books', headers=_auth(test_client.first_user.token)
    ).json()
    by_title = {book['book']['title']: book for book in books}
    assert by_title['First']['rank'] == 1
    assert by_title['Second']['rank'] is None
    assert by_title['Third']['rank'] is None
    assert body['unplaced_rankings_count'] == 2
    assert body['next_unplaced_book_id'] == by_title['Second']['book']['id']


@patch('app.services.goodreads_import.get_book_detail', return_value=None)
def test_goodreads_import_into_populated_rankings_leaves_read_book_unplaced(
    _mock_detail, test_client: TestClient
):
    headers = _auth(test_client.first_user.token)
    book = test_client.post(
        '/v1/books', headers=headers, json={'title': 'Placed'}
    ).json()
    book_id = book['id']
    response = test_client.post(
        f'/v1/users/me/books/{book_id}', headers=headers, json={'on_rankings': True}
    )
    assert response.json()['rank'] == 1

    body = _upload(test_client, test_client.first_user.token).json()
    books = test_client.get('/v1/users/me/books', headers=headers).json()
    dune = next(book for book in books if book['book']['title'] == 'Dune')
    assert dune['rank'] is None
    assert body['unplaced_rankings_count'] == 1
    assert body['next_unplaced_book_id'] == dune['book']['id']


@patch('app.services.goodreads_import.get_book_detail', return_value=None)
@patch('app.services.goodreads_import.utc_now')
def test_goodreads_import_defaults_missing_read_date_to_import_day(
    mock_now, _mock_detail, test_client: TestClient
):
    mock_now.return_value = datetime(2026, 8, 14, 15, tzinfo=timezone.utc)
    content = HEADER + 'Undated,Author,="",="9780000000001",0,100,2020,2020,read,,\n'
    _upload(test_client, test_client.first_user.token, content)
    books = test_client.get(
        '/v1/users/me/books', headers=_auth(test_client.first_user.token)
    ).json()
    assert books[0]['completed_at'] == '2026-08-14'


def test_goodreads_import_rejects_non_export(test_client: TestClient):
    body = _upload(
        test_client, test_client.first_user.token, 'just,some,columns\n1,2,3\n'
    ).json()
    assert body['books_created'] == 0
    assert body['skipped'][0]['reason'].startswith('Not a Goodreads export')


def test_goodreads_import_requires_auth(test_client: TestClient):
    response = test_client.post(
        '/v1/users/me/import/goodreads',
        files={'file': ('x.csv', io.BytesIO(b'Title\n'), 'text/csv')},
    )
    assert response.status_code == 401
