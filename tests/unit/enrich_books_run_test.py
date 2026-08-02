# pylint: disable=missing-module-docstring, missing-function-docstring
"""
A missing GOOGLE_BOOKS_API_KEY disables the Google fallback silently: every
book that needs it returns None, which lands in the same "miss" bucket as a
book no source has ever heard of. A QA run read as "38 misses" when it was
really an unset variable, so the run has to say so itself.
"""

from unittest.mock import patch

from app.db.models_sandbox import DbBook
from app.migration import enrich_books


def _pending_book(session):
    session.add(
        DbBook(
            title='The Phoenix Project',
            isbn=None,
            googleid='_An-CAAAQBAJ',
            authors=None,
            year=None,
        )
    )
    session.commit()


def _run_capturing(session, capsys, api_key):
    with patch.object(enrich_books, 'SessionLocal', return_value=session), patch.object(
        enrich_books, 'resolve_book_detail', return_value=None
    ), patch.object(enrich_books.time, 'sleep'), patch.object(
        enrich_books, 'get_settings'
    ) as settings:
        settings.return_value.google_books_api_key = api_key
        session.close = lambda: None  # run() closes its own session
        enrich_books.run()
    return capsys.readouterr().out


def test_run_warns_when_the_google_key_is_missing(test_client, capsys):
    session = test_client.test_db_session
    _pending_book(session)

    out = _run_capturing(session, capsys, api_key=None)

    assert 'GOOGLE_BOOKS_API_KEY is not set' in out
    assert '1 of these books carry a googleid' in out


def test_run_stays_quiet_when_the_key_is_present(test_client, capsys):
    session = test_client.test_db_session
    _pending_book(session)

    out = _run_capturing(session, capsys, api_key='AIzaTest')

    assert 'GOOGLE_BOOKS_API_KEY' not in out
