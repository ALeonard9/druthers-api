"""
Backfill book detail (authors/year/subjects/description/pages/rating/cover)
for books that were imported without it: Open Library keyed on isbn, falling
back to Google Books keyed on the legacy googleid for the many rows whose
edition-specific ISBN Open Library does not index.

Throttled and **resumable**: it only processes books still missing detail,
so re-running continues where it left off.

Usage::

    DATABASE_URL=... ENV=prod python -m app.migration.enrich_books
"""

import time

from sqlalchemy import and_, or_

from app.db.database import SessionLocal
from app.db.models_sandbox import DbBook
from app.services.book_search import (
    UpstreamUnavailable,
    apply_detail_to_book,
    resolve_book_detail,
)

# Be polite to Open Library (they ask for gentle, identifiable traffic).
THROTTLE_SECONDS = 1.0
# Consecutive *upstream failures* — not misses. A book the source has no
# record of will never resolve however long we wait, so counting those here
# would stall the run on an unresolvable row and, because the pending set
# comes back in the same order, never get past it on a re-run either.
STOP_AFTER_CONSECUTIVE_ERRORS = 15


def pending_books(db):
    """
    Books still worth an enrichment call.

    See ``enrich_movies.pending_movies``: ``description``/``authors`` is only
    a proxy for "never enriched", so a row that has them but no ``year``
    would never be retried. Select on the missing field too.

    A row needs *some* usable key. ``googleid`` counts: Google Books is the
    fallback source, and rows imported from it may have no isbn at all.
    Ordered so a resumed run is reproducible.
    """
    return (
        db.query(DbBook)
        .filter(
            or_(DbBook.isbn.isnot(None), DbBook.googleid.isnot(None)),
            or_(
                and_(DbBook.description.is_(None), DbBook.authors.is_(None)),
                DbBook.year.is_(None),
            ),
        )
        .order_by(DbBook.id)
        .all()
    )


def run() -> None:
    """Enrich all books still missing detail."""
    db = SessionLocal()
    try:
        pending = pending_books(db)
        total = len(pending)
        print(f'{total} books to enrich')
        enriched = misses = errors = consecutive = processed = 0
        for book in pending:
            processed += 1
            try:
                detail = resolve_book_detail(book.isbn, book.googleid)
            except UpstreamUnavailable:
                errors += 1
                consecutive += 1
            else:
                consecutive = 0
                if detail:
                    # The one place titles are rewritten: these are legacy
                    # imported rows, not editions anyone chose. Adds and
                    # detail views keep the title the user picked.
                    apply_detail_to_book(book, detail, overwrite_title=True)
                    db.commit()
                    enriched += 1
                else:
                    misses += 1
            if processed % 25 == 0:
                print(
                    f'  {processed}/{total} (enriched {enriched}, '
                    f'misses {misses}, errors {errors})'
                )
            if consecutive >= STOP_AFTER_CONSECUTIVE_ERRORS:
                print(
                    f'Stopping after {consecutive} consecutive upstream '
                    'failures (likely rate limited or offline). Re-run '
                    'later to resume.'
                )
                break
            time.sleep(THROTTLE_SECONDS)
        print(
            f'Done: enriched {enriched}, misses {misses}, errors {errors}, '
            f'remaining ~{total - processed}'
        )
    finally:
        db.close()


if __name__ == '__main__':
    run()
