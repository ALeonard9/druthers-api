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
from datetime import timedelta

from sqlalchemy import and_, or_

from app.config import get_settings
from app.db.database import SessionLocal
from app.db.models_sandbox import DbBook
from app.services.book_search import (
    UpstreamUnavailable,
    apply_detail_to_book,
    resolve_book_detail,
)
from app.services.tracker_rules import utc_now

# Be polite to Open Library (they ask for gentle, identifiable traffic).
THROTTLE_SECONDS = 1.0
# Consecutive *upstream failures* — not misses. A book the source has no
# record of will never resolve however long we wait, so counting those here
# would stall the run on an unresolvable row and, because the pending set
# comes back in the same order, never get past it on a re-run either.
STOP_AFTER_CONSECUTIVE_ERRORS = 15
# A resolved-but-still-incomplete row (e.g. no upstream publishedDate, #258)
# isn't "never enriched" -- it's a permanent answer. Retry on an interval
# instead of every run, in case the missing field ever does show up upstream.
RETRY_AFTER = timedelta(days=30)


def pending_books(db):
    """
    Books still worth an enrichment call.

    See ``enrich_movies.pending_movies``: ``description``/``authors`` is only
    a proxy for "never enriched", so a row that has them but no ``year``
    would never be retried. Select on the missing field too.

    That alone re-selects a row forever once it's been resolved and is still
    missing a field no source has (#258, e.g. an upstream ``publishedDate`` of
    ``null``) -- a resolve attempt is a real answer, not a no-op, so gate
    re-selection on ``enrichment_attempted_at`` too.

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
            or_(
                DbBook.enrichment_attempted_at.is_(None),
                DbBook.enrichment_attempted_at < utc_now() - RETRY_AFTER,
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
        # Without the key the Google fallback returns None, which is
        # indistinguishable from "no source has this book" in the totals. Say
        # so once, up front, rather than leaving an operator to read a wall of
        # misses as a data problem when it is a config one.
        needs_google = sum(1 for b in pending if b.googleid)
        if needs_google and not get_settings().google_books_api_key:
            print(
                f'  WARNING: GOOGLE_BOOKS_API_KEY is not set, so the Google '
                f'fallback is disabled. {needs_google} of these books carry a '
                f'googleid and most will be counted as misses. Set the key and '
                f're-run to enrich them.'
            )
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
                # Record the attempt regardless of outcome (#258): a miss, or
                # a hit that still leaves a field null, is a real answer from
                # the source, not "never enriched" -- pending_books uses this
                # to wait RETRY_AFTER before asking again instead of re-fetching
                # the same unresolvable field every run forever.
                book.enrichment_attempted_at = utc_now()
                if detail:
                    # The one place titles are rewritten: these are legacy
                    # imported rows, not editions anyone chose. Adds and
                    # detail views keep the title the user picked.
                    apply_detail_to_book(book, detail, overwrite_title=True)
                    enriched += 1
                else:
                    misses += 1
                db.commit()
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
