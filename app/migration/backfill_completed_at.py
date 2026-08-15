"""
Recover the old site's completed dates (``g_first``) into ``completed_at``.

The orion import handled ``g_first`` inconsistently: movies conflated it into
``created_at`` (with a ``g_updated`` fallback), and books/games never fetched
it at all. This one-shot reads the orion source again and restores the real
dates, falling back to ``g_created`` (the day the row was added - the old
site's default) for completed rows that never had an explicit date.

Joins use the same natural keys as ``orion_import``: movies/tv by ``imdb``,
books by ``googleid or title`` (exactly what the import wrote into druthers's
``googleid`` column), games by ``igdb``. TV is excluded - orion never had a
per-show completed date.

Idempotent: only rows whose ``completed_at`` is currently NULL are touched,
so user edits are never clobbered and re-running is safe.

Usage::

    ORION_MYSQL_URL=mysql+pymysql://root:pw@127.0.0.1:13307/orion \\
    DATABASE_URL=postgresql://... ENV=prod \\
        python -m app.migration.backfill_completed_at [--dry-run] \\
        [--email adamleonard9@gmail.com]
"""

import argparse
import os
import sys

from sqlalchemy import create_engine, text

from app.db.database import SessionLocal
from app.db.models import DbUser
from app.db.models_sandbox import (
    DbBook,
    DbMovie,
    DbUserBook,
    DbUserMovie,
    DbUserVideoGame,
    DbVideoGame,
)

DEFAULT_EMAIL = 'adamleonard9@gmail.com'

# (label, orion SQL producing natural_key/g_first/g_created, catalog model,
#  catalog key column, tracker model, tracker fk column)
DOMAINS = (
    (
        'movies',
        '''SELECT m.imdb AS natural_key, g.g_first, g.g_created
           FROM g_user_movies g
           JOIN movies m ON m.id = g.movies_id
           WHERE g.user_id = :uid AND g.completed = 1''',
        DbMovie,
        'imdb',
        DbUserMovie,
        'movie_id',
    ),
    (
        'books',
        '''SELECT COALESCE(NULLIF(b.googleid, ''), b.title) AS natural_key,
                  g.g_first, g.g_created
           FROM g_user_books g
           JOIN books b ON b.id = g.books_id
           WHERE g.user_id = :uid AND g.completed = 1''',
        DbBook,
        'googleid',
        DbUserBook,
        'book_id',
    ),
    (
        'games',
        '''SELECT v.igdb AS natural_key, g.g_first, g.g_created
           FROM g_user_videogames g
           JOIN videogames v ON v.id = g.videogames_id
           WHERE g.user_id = :uid AND g.completed = 1''',
        DbVideoGame,
        'igdb',
        DbUserVideoGame,
        'game_id',
    ),
)


def run(  # pylint: disable=too-many-locals, too-many-branches, too-many-statements
    dry_run: bool, email: str
) -> None:
    """Execute the backfill (or report what it would do)."""
    orion_url = os.environ.get('ORION_MYSQL_URL')
    if not orion_url:
        sys.exit('ORION_MYSQL_URL is required (throwaway MySQL with the dump)')

    orion = create_engine(orion_url)
    db = SessionLocal()
    try:
        user = db.query(DbUser).filter(DbUser.email == email).first()
        if user is None:
            sys.exit(f'No druthers user with email {email}')

        with orion.connect() as conn:
            orion_uid = conn.execute(
                text('SELECT id FROM users WHERE email = :email'),
                {'email': email},
            ).scalar()
            if orion_uid is None:
                sys.exit(f'No orion user with email {email}')

            print(
                f'{"domain":8} {"matched":>8} {"g_first":>8} '
                f'{"default":>8} {"skipped":>8} {"unmatched":>10}'
            )
            for label, sql, catalog, key_col, tracker, fk_col in DOMAINS:
                rows = conn.execute(text(sql), {'uid': orion_uid}).mappings().all()
                matched = from_first = from_default = skipped = unmatched = 0
                misses = []
                for row in rows:
                    key = row['natural_key']
                    if key is None:
                        unmatched += 1
                        continue
                    item = (
                        db.query(catalog)
                        .filter(getattr(catalog, key_col) == str(key).strip())
                        .first()
                    )
                    if item is None:
                        unmatched += 1
                        misses.append(str(key)[:40])
                        continue
                    t = (
                        db.query(tracker)
                        .filter(
                            tracker.user_id == user.pk,
                            getattr(tracker, fk_col) == item.pk,
                        )
                        .first()
                    )
                    if t is None:
                        unmatched += 1
                        misses.append(f'(no tracker) {str(key)[:32]}')
                        continue
                    if t.completed_at is not None:
                        skipped += 1  # already set - never clobber
                        continue
                    source = row['g_first'] or row['g_created']
                    if source is None:
                        skipped += 1
                        continue
                    t.completed_at = source.date()
                    matched += 1
                    if row['g_first']:
                        from_first += 1
                    else:
                        from_default += 1
                print(
                    f'{label:8} {matched:8} {from_first:8} '
                    f'{from_default:8} {skipped:8} {unmatched:10}'
                )
                if misses:
                    print(
                        f'  unmatched in {label}: {", ".join(misses[:8])}'
                        + (' …' if len(misses) > 8 else '')
                    )

        if dry_run:
            db.rollback()
            print('\n(dry run - no changes were committed)')
        else:
            db.commit()
            print('\ncommitted')
    finally:
        db.close()


def main() -> None:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--email', default=DEFAULT_EMAIL)
    args = parser.parse_args()
    run(dry_run=args.dry_run, email=args.email)


if __name__ == '__main__':
    main()
