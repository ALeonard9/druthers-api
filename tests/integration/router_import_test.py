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


@patch('app.services.goodreads_import.search_books', return_value=[])
@patch('app.services.goodreads_import.get_book_detail', return_value=None)
def test_goodreads_import_creates_books_and_trackers(
    mock_detail, _mock_search, test_client: TestClient
):
    token = test_client.first_user.token
    response = _upload(test_client, token)
    assert response.status_code == 200
    body = response.json()
    assert body['books_created'] == 2
    assert body['trackers_created'] == 2
    assert body['unplaced_read_book_ids'] == []
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


@patch('app.services.goodreads_import.search_books', return_value=[])
@patch('app.services.goodreads_import.get_book_detail')
def test_goodreads_import_enriches_new_catalog_books(
    mock_detail, _mock_search, test_client: TestClient
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


@patch('app.services.goodreads_import.search_books', return_value=[])
@patch('app.services.goodreads_import.get_book_detail', return_value=None)
def test_goodreads_import_keeps_csv_fields_when_provider_misses(
    mock_detail, _mock_search, test_client: TestClient
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


@patch('app.services.goodreads_import.search_books', return_value=[])
@patch('app.services.goodreads_import.get_book_detail', return_value=None)
def test_goodreads_import_is_idempotent(
    mock_detail, _mock_search, test_client: TestClient
):
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


@patch('app.services.goodreads_import.search_books', return_value=[])
@patch('app.services.goodreads_import.get_book_detail', return_value=None)
def test_goodreads_import_promotes_but_never_demotes(
    _mock_detail, _mock_search, test_client: TestClient
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


@patch('app.services.goodreads_import.search_books', return_value=[])
@patch('app.services.goodreads_import.get_book_detail', return_value=None)
def test_goodreads_import_leaves_all_read_rows_after_the_first_unplaced(
    _mock_detail, _mock_search, test_client: TestClient
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
    assert body['unplaced_read_book_ids'] == [
        by_title['Second']['book']['id'],
        by_title['Third']['book']['id'],
    ]


@patch('app.services.goodreads_import.search_books', return_value=[])
@patch('app.services.goodreads_import.get_book_detail', return_value=None)
def test_goodreads_import_into_populated_rankings_leaves_read_book_unplaced(
    _mock_detail, _mock_search, test_client: TestClient
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
    assert body['unplaced_read_book_ids'] == [dune['book']['id']]


@patch('app.services.goodreads_import.search_books', return_value=[])
@patch('app.services.goodreads_import.get_book_detail', return_value=None)
@patch('app.services.goodreads_import.utc_now')
def test_goodreads_import_defaults_missing_read_date_to_import_day(
    mock_now, _mock_detail, _mock_search, test_client: TestClient
):
    mock_now.return_value = datetime(2026, 8, 14, 15, tzinfo=timezone.utc)
    content = HEADER + 'Undated,Author,="",="9780000000001",0,100,2020,2020,read,,\n'
    _upload(test_client, test_client.first_user.token, content)
    books = test_client.get(
        '/v1/users/me/books', headers=_auth(test_client.first_user.token)
    ).json()
    assert books[0]['completed_at'] == '2026-08-14'


@patch('app.services.goodreads_import.get_book_detail')
@patch('app.services.goodreads_import.search_books')
def test_goodreads_import_enriches_isbnless_rows_by_title_and_author(
    mock_search, mock_detail, test_client: TestClient
):
    mock_search.return_value = [
        {
            'title': 'The Great Gatsby',
            'authors': 'F. Scott Fitzgerald',
            'isbn': '9780743273565',
            'year': '1925',
            'poster_url': 'https://covers.openlibrary.org/b/id/7222246-L.jpg',
        },
        {
            'title': 'The Great Gatsby',
            'authors': 'Some Other Author',
            'isbn': '9780000000000',
            'year': '2000',
            'poster_url': 'https://covers.openlibrary.org/b/id/1-L.jpg',
        },
    ]
    mock_detail.side_effect = [
        None,
        {
            'title': 'The Great Gatsby',
            'authors': 'F. Scott Fitzgerald',
            'isbn': '9780743273565',
            'year': 1925,
            'page_count': 180,
            'poster_url': 'https://covers.openlibrary.org/b/id/7222246-L.jpg',
        },
    ]
    content = (
        HEADER + 'The Great Gatsby,F. Scott Fitzgerald,="","",0,218,2004,1925,read,,\n'
    )

    response = _upload(test_client, test_client.first_user.token, content)

    assert response.status_code == 200
    books = test_client.get(
        '/v1/users/me/books', headers=_auth(test_client.first_user.token)
    ).json()
    gatsby = books[0]['book']
    assert gatsby['title'] == 'The Great Gatsby'
    assert gatsby['authors'] == 'F. Scott Fitzgerald'
    assert gatsby['isbn'] == '9780743273565'
    assert gatsby['poster_url'] == 'https://covers.openlibrary.org/b/id/7222246-L.jpg'
    assert gatsby['page_count'] == 180
    mock_search.assert_called_once_with('The Great Gatsby F. Scott Fitzgerald')
    assert mock_detail.call_args_list[1].args == ('9780743273565',)


@patch('app.services.goodreads_import.get_book_detail')
@patch('app.services.goodreads_import.search_books')
def test_goodreads_import_retries_an_unresolved_isbn_by_title_and_author(
    mock_search, mock_detail, test_client: TestClient
):
    mock_search.return_value = [
        {
            'title': 'The Catcher in the Rye',
            'authors': 'J. D. Salinger',
            'isbn': '9780316769488',
            'year': '1951',
            'poster_url': 'https://covers.openlibrary.org/b/id/8231856-L.jpg',
        }
    ]
    mock_detail.side_effect = [
        None,
        {
            'title': 'The Catcher in the Rye',
            'authors': 'J. D. Salinger',
            'isbn': '9780316769488',
            'year': 1951,
            'poster_url': 'https://covers.openlibrary.org/b/id/8231856-L.jpg',
        },
    ]
    content = (
        HEADER
        + 'The Catcher in the Rye,J. D. Salinger,="","9780000000000",0,234,2014,1951,read,,\n'
    )

    _upload(test_client, test_client.first_user.token, content)

    books = test_client.get(
        '/v1/users/me/books', headers=_auth(test_client.first_user.token)
    ).json()
    catcher = books[0]['book']
    assert catcher['isbn'] == '9780316769488'
    assert catcher['poster_url'] == 'https://covers.openlibrary.org/b/id/8231856-L.jpg'
    mock_search.assert_called_once_with('The Catcher in the Rye J. D. Salinger')
    assert mock_detail.call_args_list[0].args == ('9780000000000',)
    assert mock_detail.call_args_list[1].args == ('9780316769488',)


@patch('app.services.goodreads_import.get_book_detail')
@patch('app.services.goodreads_import.search_books')
def test_goodreads_import_strips_series_qualifier_from_title_for_search(
    mock_search, mock_detail, test_client: TestClient
):
    mock_search.return_value = [
        {
            'title': "Harry Potter and the Philosopher's Stone",
            'authors': 'J.K. Rowling',
            'isbn': '9780747532743',
            'year': '1997',
            'poster_url': 'https://covers.openlibrary.org/b/id/10521270-L.jpg',
        }
    ]
    mock_detail.side_effect = [
        None,
        {
            'title': "Harry Potter and the Philosopher's Stone",
            'authors': 'J.K. Rowling',
            'isbn': '9780747532743',
            'year': 1997,
            'poster_url': 'https://covers.openlibrary.org/b/id/10521270-L.jpg',
        },
    ]
    content = (
        HEADER
        + '"Harry Potter and the Philosopher\'s Stone (Harry Potter, #1)",'
        + 'J.K. Rowling,="","9780000000000",0,223,1997,1997,read,,\n'
    )

    _upload(test_client, test_client.first_user.token, content)

    books = test_client.get(
        '/v1/users/me/books', headers=_auth(test_client.first_user.token)
    ).json()
    hp = books[0]['book']
    assert hp['title'] == 'Harry Potter and the Philosopher\'s Stone (Harry Potter, #1)'
    assert hp['poster_url'] == 'https://covers.openlibrary.org/b/id/10521270-L.jpg'
    mock_search.assert_called_once_with(
        "Harry Potter and the Philosopher's Stone J.K. Rowling"
    )


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
