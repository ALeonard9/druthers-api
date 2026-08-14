# pylint: disable=missing-module-docstring, missing-function-docstring
import io

from fastapi.testclient import TestClient

HEADER = (
    'Title,Author,ISBN,ISBN13,My Rating,Number of Pages,Year Published,'
    'Original Publication Year,Exclusive Shelf,My Review\n'
)

CSV = (
    HEADER
    + 'Dune,Frank Herbert,="0441172717",="9780441172719",5,412,1990,1965,read,A classic.\n'
    + 'Piranesi,Susanna Clarke,="",="9781635575637",0,245,2020,2020,to-read,\n'
    + ',Nobody,="",="",0,,,,read,\n'
)


def _auth(token: str) -> dict:
    return {'Authorization': f'Bearer {token}'}


def _upload(test_client: TestClient, token: str, content: str = CSV):
    return test_client.post(
        '/v1/users/me/import/goodreads',
        headers=_auth(token),
        files={'file': ('goodreads.csv', io.BytesIO(content.encode()), 'text/csv')},
    )


def test_goodreads_import_creates_books_and_trackers(test_client: TestClient):
    token = test_client.first_user.token
    response = _upload(test_client, token)
    assert response.status_code == 200
    body = response.json()
    assert body['books_created'] == 2
    assert body['trackers_created'] == 2
    assert body['skipped'] == [{'row': 4, 'reason': 'Missing title'}]

    books = test_client.get('/v1/users/me/books', headers=_auth(token)).json()
    by_title = {b['book']['title']: b for b in books}
    dune = by_title['Dune']
    assert dune['on_rankings'] is True
    assert dune['on_watchlist'] is False
    assert dune['book']['isbn'] == '9780441172719'
    assert dune['book']['year'] == 1965
    assert 'A classic.' in dune['notes']
    assert 'Goodreads rating: 5/5' in dune['notes']
    piranesi = by_title['Piranesi']
    assert piranesi['on_watchlist'] is True
    assert piranesi['on_rankings'] is False


def test_goodreads_import_is_idempotent(test_client: TestClient):
    token = test_client.first_user.token
    _upload(test_client, token)
    body = _upload(test_client, token).json()
    assert body['books_created'] == 0
    assert body['books_matched'] == 2
    assert body['trackers_created'] == 0
    assert body['trackers_updated'] == 0
    books = test_client.get('/v1/users/me/books', headers=_auth(token)).json()
    assert len(books) == 2


def test_goodreads_import_promotes_but_never_demotes(test_client: TestClient):
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


def test_goodreads_import_rejects_oversized_file(test_client: TestClient):
    token = test_client.first_user.token
    content = b'x' * (5 * 1024 * 1024 + 1)
    response = test_client.post(
        '/v1/users/me/import/goodreads',
        headers=_auth(token),
        files={'file': ('huge.csv', io.BytesIO(content), 'text/csv')},
    )
    assert response.status_code == 413
