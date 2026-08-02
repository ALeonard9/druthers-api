# pylint: disable=missing-module-docstring, missing-function-docstring
from unittest.mock import patch

from app.db.models_sandbox import DbBook
from app.migration import repair_book_language


def test_suspect_books_selects_neither_eng_nor_null(test_client):
    session = test_client.test_db_session
    session.add_all(
        [
            DbBook(title='The Stand', isbn='9780307743688', language='rus'),
            DbBook(title='Fine (eng)', isbn='9780000000001', language='eng'),
            DbBook(title='Fine (en)', isbn='9780000000002', language='en'),
            DbBook(title='Fine (null)', isbn='9780000000003', language=None),
        ]
    )
    session.commit()

    assert [b.title for b in repair_book_language.suspect_books(session)] == [
        'The Stand'
    ]


def _run_capturing(session, capsys, detail, dry_run):
    with patch.object(
        repair_book_language, 'SessionLocal', return_value=session
    ), patch.object(
        repair_book_language, 'resolve_book_detail', return_value=detail
    ), patch.object(
        repair_book_language.time, 'sleep'
    ):
        session.close = lambda: None  # run() closes its own session
        repair_book_language.run(dry_run=dry_run)
    return capsys.readouterr().out


def test_dry_run_reports_but_does_not_write(test_client, capsys):
    session = test_client.test_db_session
    session.add(DbBook(title='The Stand', isbn='9780307743688', language='rus'))
    session.commit()

    out = _run_capturing(session, capsys, detail={'language': 'eng'}, dry_run=True)

    assert 'rus -> eng' in out
    assert 'dry run' in out
    book = session.query(DbBook).filter(DbBook.title == 'The Stand').one()
    assert book.language == 'rus'


def test_apply_writes_only_the_language_field(test_client, capsys):
    session = test_client.test_db_session
    session.add(
        DbBook(
            title='The Stand',
            isbn='9780307743688',
            language='rus',
            authors='Someone Else',
        )
    )
    session.commit()

    _run_capturing(
        session,
        capsys,
        detail={'language': 'eng', 'authors': 'Should not be written'},
        dry_run=False,
    )

    book = session.query(DbBook).filter(DbBook.title == 'The Stand').one()
    assert book.language == 'eng'
    assert book.authors == 'Someone Else'


def test_resolve_returning_the_same_language_is_left_unchanged(test_client, capsys):
    session = test_client.test_db_session
    session.add(DbBook(title='Untranslatable', isbn='9780307743688', language='rus'))
    session.commit()

    out = _run_capturing(session, capsys, detail={'language': 'rus'}, dry_run=False)

    assert 'Done: 0 repaired, 1 unchanged' in out
