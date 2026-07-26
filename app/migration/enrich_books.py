"""
Backfill Open Library detail (authors/year/subjects/description/pages/
rating/cover) for books that were imported without it, keyed on isbn.
Throttled and **resumable**: it only processes books still missing detail,
so re-running continues where it left off.

Usage::

    DATABASE_URL=... ENV=prod python -m app.migration.enrich_books
"""

import time

from sqlalchemy import and_, or_

from app.db.database import SessionLocal
from app.db.models_sandbox import DbBook
from app.services.book_search import apply_detail_to_book, get_book_detail

# Be polite to Open Library (they ask for gentle, identifiable traffic).
THROTTLE_SECONDS = 1.0
# get_book_detail returns None both for genuine misses and rate limiting; a
# run of consecutive Nones almost certainly means we are being throttled.
STOP_AFTER_CONSECUTIVE_MISSES = 15


def pending_books(db):
    """
    Books still worth an Open Library call.

    See ``enrich_movies.pending_movies``: ``description``/``authors`` is only
    a proxy for "never enriched", so a row that has them but no ``year``
    would never be retried. Select on the missing field too.
    """
    return (
        db.query(DbBook)
        .filter(
            DbBook.isbn.isnot(None),
            or_(
                and_(DbBook.description.is_(None), DbBook.authors.is_(None)),
                DbBook.year.is_(None),
            ),
        )
        .all()
    )


def run() -> None:
    """Enrich all books still missing detail."""
    db = SessionLocal()
    try:
        pending = pending_books(db)
        total = len(pending)
        print(f'{total} books to enrich')
        enriched = misses = consecutive = processed = 0
        for book in pending:
            processed += 1
            detail = get_book_detail(book.isbn)
            if detail:
                apply_detail_to_book(book, detail)
                db.commit()
                enriched += 1
                consecutive = 0
            else:
                misses += 1
                consecutive += 1
            if processed % 25 == 0:
                print(f'  {processed}/{total} (enriched {enriched}, misses {misses})')
            if consecutive >= STOP_AFTER_CONSECUTIVE_MISSES:
                print(
                    f'Stopping after {consecutive} consecutive misses '
                    '(likely Open Library rate limit). Re-run later to resume.'
                )
                break
            time.sleep(THROTTLE_SECONDS)
        print(
            f'Done: enriched {enriched}, misses {misses}, '
            f'remaining ~{total - processed}'
        )
    finally:
        db.close()


if __name__ == '__main__':
    run()
