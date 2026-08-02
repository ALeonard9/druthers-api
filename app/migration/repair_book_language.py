"""
One-time repair for #254: 88 of 223 prod books carry a wrong ``language``,
inherited from Open Library's pre-#251 work-level language field (the union
of every edition's languages, so a work with a Russian translation could
report ``rus`` for an English book -- e.g. *The Da Vinci Code* as ``mal``).
``_language()`` was fixed in #251, but only for rows enriched after it.

``enrich_books`` cannot repair these rows on its own: ``apply_detail_to_book``
skips ``None`` so a re-run can only replace a bad value with a different
non-null one, and ``pending_books()`` doesn't select them -- they already
have ``authors``/``year``, the fields it selects on.

Only rewrites ``language``; every other field is left alone. Throttled,
resumable, and **dry-run by default** -- pass ``--apply`` to actually write.

Usage::

    DATABASE_URL=... ENV=prod python -m app.migration.repair_book_language
    DATABASE_URL=... ENV=prod python -m app.migration.repair_book_language --apply
"""

import argparse
import time

from app.db.database import SessionLocal
from app.db.models_sandbox import DbBook
from app.services.book_search import UpstreamUnavailable, resolve_book_detail

# Be polite to Open Library (they ask for gentle, identifiable traffic).
THROTTLE_SECONDS = 1.0
STOP_AFTER_CONSECUTIVE_ERRORS = 15
# Values today's _language() would actually produce. Anything else on a row
# is suspect -- inherited from the pre-#251 work-level union bug.
_TRUSTED_LANGUAGES = ('eng', 'en')


def suspect_books(db):
    """Books whose language is neither eng/en/NULL -- see #254."""
    return (
        db.query(DbBook)
        .filter(
            DbBook.language.isnot(None),
            ~DbBook.language.in_(_TRUSTED_LANGUAGES),
        )
        .order_by(DbBook.id)
        .all()
    )


def run(throttle: float = THROTTLE_SECONDS, dry_run: bool = True) -> None:
    """Re-resolve and repair ``language`` for suspect rows."""
    db = SessionLocal()
    try:
        pending = suspect_books(db)
        total = len(pending)
        print(f'{total} books with a suspect language')
        if dry_run:
            print('(dry run -- no writes; pass --apply to write)')
        fixed = unchanged = errors = consecutive = processed = 0
        for book in pending:
            processed += 1
            try:
                detail = resolve_book_detail(book.isbn, book.googleid)
            except UpstreamUnavailable:
                errors += 1
                consecutive += 1
            else:
                consecutive = 0
                new_language = (detail or {}).get('language')
                if new_language and new_language != book.language:
                    print(f'  {book.title[:50]:<50} {book.language} -> {new_language}')
                    if not dry_run:
                        book.language = new_language
                        db.commit()
                    fixed += 1
                else:
                    unchanged += 1
            if processed % 25 == 0:
                print(
                    f'  {processed}/{total} (fixed {fixed}, '
                    f'unchanged {unchanged}, errors {errors})'
                )
            if consecutive >= STOP_AFTER_CONSECUTIVE_ERRORS:
                print(
                    f'Stopping after {consecutive} consecutive upstream '
                    'failures (likely rate limited or offline). Re-run '
                    'later to resume.'
                )
                break
            time.sleep(throttle)
        print(
            f'Done: {fixed} repaired, {unchanged} unchanged/unresolved, '
            f'{errors} errors, remaining ~{total - processed}'
        )
    finally:
        db.close()


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description='Repair wrong book language values (#254).'
    )
    parser.add_argument(
        '--throttle',
        type=float,
        default=THROTTLE_SECONDS,
        help=f'seconds between resolves (default {THROTTLE_SECONDS})',
    )
    parser.add_argument(
        '--apply',
        dest='dry_run',
        action='store_false',
        help='write changes (default is dry-run, report only)',
    )
    args = parser.parse_args()
    run(throttle=args.throttle, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
