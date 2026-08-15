"""
Cover URL hygiene for books and games (#163).

Both search services already emit correct URLs for anything added today -
``book_search._cover`` builds covers.openlibrary.org and ``game_search``
serves IGDB's ``t_cover_big_2x``. This only repairs rows imported before
that, so it is a pure data backfill with no service changes:

* **Books** - 221 covers still point at books.google.com, left over from the
  Google Books era. Re-pointed to Open Library, keyed on the row's ISBN.
* **Games** - legacy covers use IGDB's ``t_thumb`` (~90px, blurry at the
  size the UI renders). Upgraded by swapping the size token in the URL.

The two domains need different care:

Open Library serves a **blank placeholder with HTTP 200** for an ISBN it has
no cover for - only ``?default=false`` turns that into a 404. So every book
candidate is verified before it is written; without that check this would
silently trade working Google covers for grey boxes.

IGDB renders every size from the same ``image_id``, so a cover that exists
at ``t_thumb`` exists at the target size too. That substitution is
deterministic and needs no network call.

Usage::

    DATABASE_URL=... ENV=prod python -m app.migration.backfill_covers --dry-run
    DATABASE_URL=... ENV=prod python -m app.migration.backfill_covers
"""

import argparse
import re
import time
from typing import Optional

import requests

from app.db.database import SessionLocal
from app.db.models_sandbox import DbBook, DbVideoGame
from app.log.logging_config import logger
from app.services.game_search import COVER_URL

REQUEST_TIMEOUT = 10
# Only books hit the network (one HEAD each); 221 rows at ~4/s is under a
# minute. Games need no requests at all.
DEFAULT_THROTTLE_SECONDS = 0.25

# Hosts that mean "this cover predates the Open Library switch".
_LEGACY_BOOK_HOSTS = ('books.google.com', 'books.googleusercontent.com')

# Two different "can't fix this" reasons, deliberately not conflated (#259):
# a missing/malformed isbn is a data gap worth someone's attention, while a
# valid isbn Open Library simply has no cover for is the *expected*, correct
# post-#251 state for a book that's on Google Books for exactly that reason.
_REASON_NO_ISBN = 'no usable ISBN'
_REASON_NO_COVER = 'no Open Library cover'
# ``default=false`` is load-bearing - see the module docstring.
_OPENLIBRARY_COVER = 'https://covers.openlibrary.org/b/isbn/{isbn}-L.jpg'
_VERIFY_SUFFIX = '?default=false'

# The size token the service currently serves, taken from game_search's own
# constant so the two can never drift apart.
_TARGET_SIZE = COVER_URL.rstrip('/').rsplit('/', 1)[-1]
_IGDB_SIZE_RE = re.compile(r'/t_[a-z0-9_]+/')


def _is_legacy_book_cover(poster_url: Optional[str]) -> bool:
    """True for a cover still served by Google Books."""
    if not poster_url:
        return False
    return any(host in poster_url for host in _LEGACY_BOOK_HOSTS)


def openlibrary_cover_url(isbn: Optional[str]) -> Optional[str]:
    """
    Open Library cover URL for ``isbn``, or None when the row has no usable
    ISBN. Hyphens and spacing vary across the imported data, so they're
    stripped - Open Library wants the bare digits (X is a valid check digit).
    """
    if not isbn:
        return None
    cleaned = re.sub(r'[^0-9Xx]', '', isbn).upper()
    if len(cleaned) not in (10, 13):
        return None
    return _OPENLIBRARY_COVER.format(isbn=cleaned)


def upgrade_igdb_size(poster_url: Optional[str]) -> Optional[str]:
    """
    Rewrite an IGDB cover URL to the size the service serves today. Returns
    None when there's nothing to do - not an IGDB URL, or already correct.
    """
    if not poster_url or 'images.igdb.com' not in poster_url:
        return None
    if f'/{_TARGET_SIZE}/' in poster_url:
        return None
    upgraded = _IGDB_SIZE_RE.sub(f'/{_TARGET_SIZE}/', poster_url, count=1)
    return upgraded if upgraded != poster_url else None


def _cover_exists(url: str) -> bool:
    """
    HEAD the candidate with ``default=false`` so a missing cover 404s instead
    of returning Open Library's blank placeholder. Redirects are followed -
    a real cover answers with a 302 to the CDN.
    """
    try:
        response = requests.head(
            f'{url}{_VERIFY_SUFFIX}',
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True,
        )
        return response.status_code == 200
    except requests.RequestException as exc:
        logger.warning('Open Library cover check failed for %s: %s', url, exc)
        return False


def backfill_books(db, throttle: float, dry_run: bool) -> tuple:
    """
    Re-point Google Books covers at Open Library. Returns ``(fixed, actionable,
    expected)``: ``actionable`` is a list of ``(title, reason)`` worth a look;
    ``expected`` is a count of rows correctly left on Google Books because
    Open Library has no cover for their (perfectly valid) ISBN -- the normal
    post-#251 state, not a problem (#259).
    """
    rows = db.query(DbBook).all()
    pending = [b for b in rows if _is_legacy_book_cover(b.poster_url)]
    print(f'{len(pending)} book covers still on Google Books')

    fixed = 0
    actionable = []
    expected = 0
    for book in pending:
        candidate = openlibrary_cover_url(book.isbn)
        if not candidate:
            actionable.append((book.title, _REASON_NO_ISBN))
            continue
        if not _cover_exists(candidate):
            # Leaving the Google cover in place beats a blank placeholder --
            # and is correct, not outstanding: this ISBN just isn't one Open
            # Library has a cover for.
            expected += 1
            time.sleep(throttle)
            continue
        if not dry_run:
            book.poster_url = candidate
            db.commit()
        fixed += 1
        time.sleep(throttle)

    return fixed, actionable, expected


def backfill_games(db, dry_run: bool) -> int:
    """Upgrade legacy IGDB cover sizes. Returns the number changed."""
    rows = db.query(DbVideoGame).all()
    fixed = 0
    for game in rows:
        upgraded = upgrade_igdb_size(game.poster_url)
        if not upgraded:
            continue
        if not dry_run:
            game.poster_url = upgraded
        fixed += 1
    if not dry_run:
        db.commit()
    print(f'{fixed} game covers upgraded to {_TARGET_SIZE}')
    return fixed


def run(throttle: float = DEFAULT_THROTTLE_SECONDS, dry_run: bool = False) -> None:
    """Repair legacy book and game cover URLs."""
    db = SessionLocal()
    try:
        if dry_run:
            print('(dry run - no writes)')
        books_fixed, books_actionable, books_expected = backfill_books(
            db, throttle, dry_run
        )
        games_fixed = backfill_games(db, dry_run)
        print(
            f'\nDone: {books_fixed} book covers re-pointed, '
            f'{games_fixed} game covers upgraded'
        )
        if books_expected:
            print(
                f'{books_expected} books correctly stay on Google Books '
                f'(Open Library has no cover for their ISBN - expected, not a problem)'
            )
        if books_actionable:
            print(f'\n{len(books_actionable)} books need attention:')
            for title, reason in books_actionable:
                print(f'  {title[:60]:<60} {reason}')
    finally:
        db.close()


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description='Repair legacy cover URLs.')
    parser.add_argument(
        '--throttle',
        type=float,
        default=DEFAULT_THROTTLE_SECONDS,
        help=f'seconds between book checks (default {DEFAULT_THROTTLE_SECONDS})',
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='report what would change without writing',
    )
    args = parser.parse_args()
    run(throttle=args.throttle, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
