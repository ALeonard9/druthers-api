# pylint: disable=missing-module-docstring, missing-function-docstring
# pylint: disable=protected-access
from unittest.mock import MagicMock, patch

from app.db.models_sandbox import DbBook
from app.migration import backfill_covers


def test_detects_legacy_book_hosts():
    assert backfill_covers._is_legacy_book_cover(
        'http://books.google.com/books/content?id=x'
    )
    assert backfill_covers._is_legacy_book_cover(
        'https://books.googleusercontent.com/x.jpg'
    )


def test_openlibrary_covers_are_left_alone():
    assert not backfill_covers._is_legacy_book_cover(
        'https://covers.openlibrary.org/b/id/123-L.jpg'
    )
    assert not backfill_covers._is_legacy_book_cover(None)


def test_openlibrary_url_normalizes_isbn():
    # Hyphenation varies across the imported data.
    assert (
        backfill_covers.openlibrary_cover_url('978-0-441-01359-3')
        == 'https://covers.openlibrary.org/b/isbn/9780441013593-L.jpg'
    )
    # X is a valid ISBN-10 check digit and must survive.
    assert backfill_covers.openlibrary_cover_url('080442957x').endswith(
        '080442957X-L.jpg'
    )


def test_openlibrary_url_rejects_unusable_isbn():
    assert backfill_covers.openlibrary_cover_url(None) is None
    assert backfill_covers.openlibrary_cover_url('') is None
    assert backfill_covers.openlibrary_cover_url('12345') is None


def test_igdb_size_upgrade():
    assert (
        backfill_covers.upgrade_igdb_size(
            'https://images.igdb.com/igdb/image/upload/t_thumb/co1r7f.jpg'
        )
        == 'https://images.igdb.com/igdb/image/upload/t_cover_big_2x/co1r7f.jpg'
    )


def test_igdb_upgrade_skips_current_size_and_foreign_urls():
    # Already at the size the service serves - nothing to do.
    assert (
        backfill_covers.upgrade_igdb_size(
            'https://images.igdb.com/igdb/image/upload/t_cover_big_2x/co1r7f.jpg'
        )
        is None
    )
    assert (
        backfill_covers.upgrade_igdb_size('https://example.com/t_thumb/x.jpg') is None
    )
    assert backfill_covers.upgrade_igdb_size(None) is None


def test_target_size_tracks_the_service_constant():
    """The target is derived from game_search.COVER_URL so they can't drift."""
    assert backfill_covers.COVER_URL.endswith(backfill_covers._TARGET_SIZE)


@patch('app.migration.backfill_covers.requests.head')
def test_cover_exists_requires_default_false(mock_head):
    """
    Open Library returns a blank placeholder with HTTP 200 unless
    default=false is passed, so the check must send it.
    """
    mock_head.return_value = MagicMock(status_code=200)
    assert backfill_covers._cover_exists(
        'https://covers.openlibrary.org/b/isbn/9780441013593-L.jpg'
    )
    called_url = mock_head.call_args[0][0]
    assert called_url.endswith('?default=false')
    assert mock_head.call_args.kwargs['allow_redirects'] is True


@patch('app.migration.backfill_covers.requests.head')
def test_missing_cover_is_not_written(mock_head):
    mock_head.return_value = MagicMock(status_code=404)
    assert not backfill_covers._cover_exists(
        'https://covers.openlibrary.org/b/isbn/9999999999999-L.jpg'
    )


@patch('app.migration.backfill_covers.requests.head')
def test_network_failure_leaves_cover_alone(mock_head):
    mock_head.side_effect = backfill_covers.requests.RequestException('boom')
    assert not backfill_covers._cover_exists(
        'https://covers.openlibrary.org/b/isbn/9780441013593-L.jpg'
    )


@patch('app.migration.backfill_covers._cover_exists', return_value=True)
def test_backfill_books_repoints_when_openlibrary_has_a_cover(
    _mock_exists, test_client
):
    session = test_client.test_db_session
    book = DbBook(
        title='Has a cover',
        isbn='9780441013593',
        poster_url='http://books.google.com/books/content?id=x',
    )
    session.add(book)
    session.commit()

    fixed, actionable, expected = backfill_covers.backfill_books(
        session, throttle=0, dry_run=False
    )

    assert fixed == 1
    assert not actionable
    assert expected == 0
    assert (
        book.poster_url == 'https://covers.openlibrary.org/b/isbn/9780441013593-L.jpg'
    )


@patch('app.migration.backfill_covers._cover_exists', return_value=False)
def test_backfill_books_treats_no_openlibrary_cover_as_expected_not_actionable(
    _mock_exists, test_client
):
    # #259: a valid ISBN Open Library simply has no cover for is the correct,
    # expected post-#251 state -- not something to flag for a look.
    session = test_client.test_db_session
    book = DbBook(
        title='Valid ISBN, no OL cover',
        isbn='9780441013593',
        poster_url='http://books.google.com/books/content?id=x',
    )
    session.add(book)
    session.commit()

    fixed, actionable, expected = backfill_covers.backfill_books(
        session, throttle=0, dry_run=False
    )

    assert fixed == 0
    assert not actionable
    assert expected == 1
    assert book.poster_url == 'http://books.google.com/books/content?id=x'


def test_backfill_books_flags_unusable_isbn_as_actionable(test_client):
    session = test_client.test_db_session
    book = DbBook(
        title='No usable ISBN',
        isbn='HARVARD:HWB4C3',
        poster_url='http://books.google.com/books/content?id=x',
    )
    session.add(book)
    session.commit()

    fixed, actionable, expected = backfill_covers.backfill_books(
        session, throttle=0, dry_run=False
    )

    assert fixed == 0
    assert expected == 0
    assert actionable == [('No usable ISBN', backfill_covers._REASON_NO_ISBN)]
