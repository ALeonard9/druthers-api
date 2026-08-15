"""
Recover the remaining orion timestamps (#160):

1. **Episode watch dates** - ``g_user_tvepisodes.g_first`` was fetched by the
   import but never used, so every pre-cutover watch mark carries an
   import-time timestamp. Restores ``watched_at = COALESCE(g_first,
   g_created)`` for watched marks, keyed deterministically through the TVMaze
   episode id.
2. **Tracker "date added"** - ``g_created`` was dropped (books/games/tv) or
   overwritten (movies), so ``created_at`` is wrong across the tracker
   tables. Corrects it from the orion source using the import's natural keys.

Idempotent: watched_at only fills NULLs; created_at only changes when it
differs from the orion value.

Usage::

    ORION_MYSQL_URL=mysql+pymysql://root:pw@127.0.0.1:13307/orion \\
    DATABASE_URL=postgresql://... ENV=prod \\
        python -m app.migration.backfill_orion_timestamps [--dry-run] \\
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
    DbTVEpisode,
    DbTVShow,
    DbUserBook,
    DbUserMovie,
    DbUserTVEpisode,
    DbUserTVShow,
    DbUserVideoGame,
    DbVideoGame,
)

DEFAULT_EMAIL = 'adamleonard9@gmail.com'

# Tracker created_at recovery: same natural keys the import used.
TRACKER_DOMAINS = (
    (
        'movies',
        '''SELECT m.imdb AS natural_key, g.g_created
           FROM g_user_movies g JOIN movies m ON m.id = g.movies_id
           WHERE g.user_id = :uid''',
        DbMovie,
        'imdb',
        DbUserMovie,
        'movie_id',
    ),
    (
        'tv',
        '''SELECT t.tvmaze AS natural_key, g.g_created
           FROM g_user_tv g JOIN tv t ON t.id = g.tv_id
           WHERE g.user_id = :uid''',
        DbTVShow,
        'tvmaze',
        DbUserTVShow,
        'tv_show_id',
    ),
    (
        'books',
        '''SELECT COALESCE(NULLIF(b.googleid, ''), b.title) AS natural_key,
                  g.g_created
           FROM g_user_books g JOIN books b ON b.id = g.books_id
           WHERE g.user_id = :uid''',
        DbBook,
        'googleid',
        DbUserBook,
        'book_id',
    ),
    (
        'games',
        '''SELECT v.igdb AS natural_key, g.g_created
           FROM g_user_videogames g JOIN videogames v ON v.id = g.videogames_id
           WHERE g.user_id = :uid''',
        DbVideoGame,
        'igdb',
        DbUserVideoGame,
        'game_id',
    ),
)

EPISODE_SQL = '''
    SELECT e.tvmaze AS episode_tvmaze,
           COALESCE(g.g_first, g.g_created) AS watched_source
    FROM g_user_tvepisodes g
    JOIN tvepisodes e ON e.id = g.tvepisode_id
    WHERE g.user_id = :uid AND g.watched = 1
'''


def _resolve_users(db, conn, email):
    user = db.query(DbUser).filter(DbUser.email == email).first()
    if user is None:
        sys.exit(f'No druthers user with email {email}')
    orion_uid = conn.execute(
        text('SELECT id FROM users WHERE email = :email'), {'email': email}
    ).scalar()
    if orion_uid is None:
        sys.exit(f'No orion user with email {email}')
    return user, orion_uid


def _backfill_episodes(db, conn, user, orion_uid) -> None:
    rows = conn.execute(text(EPISODE_SQL), {'uid': orion_uid}).mappings().all()
    # One pass: map tvmaze episode id -> druthers tracker, then fill.
    episode_by_tvmaze = {
        tvmaze: (pk, watched_at)
        for tvmaze, pk, watched_at in (
            db.query(DbTVEpisode.tvmaze, DbUserTVEpisode.pk, DbUserTVEpisode.watched_at)
            .join(DbUserTVEpisode, DbUserTVEpisode.episode_id == DbTVEpisode.pk)
            .filter(DbUserTVEpisode.user_id == user.pk, DbTVEpisode.tvmaze.isnot(None))
            .all()
        )
    }
    filled = skipped = unmatched = 0
    for row in rows:
        target = episode_by_tvmaze.get(row['episode_tvmaze'])
        if target is None or row['watched_source'] is None:
            unmatched += 1
            continue
        pk, existing = target
        if existing is not None:
            skipped += 1
            continue
        db.query(DbUserTVEpisode).filter(DbUserTVEpisode.pk == pk).update(
            {
                DbUserTVEpisode.watched_at: row['watched_source'],
                # Pin updated_at to itself: Column(onupdate=...) fires on ANY
                # SQLAlchemy UPDATE unless the column is explicitly set, and a
                # backfill must never re-date rows in the Activity feed.
                DbUserTVEpisode.updated_at: DbUserTVEpisode.updated_at,
            },
            synchronize_session=False,
        )
        filled += 1
    print(
        f'{"episodes":10} filled={filled:6} skipped={skipped:6} unmatched={unmatched:6}'
    )


def _backfill_created_at(  # pylint: disable=too-many-locals
    db, conn, user, orion_uid
) -> None:
    for label, sql, catalog, key_col, tracker, fk_col in TRACKER_DOMAINS:
        rows = conn.execute(text(sql), {'uid': orion_uid}).mappings().all()
        fixed = same = unmatched = 0
        for row in rows:
            key = row['natural_key']
            if key is None or row['g_created'] is None:
                unmatched += 1
                continue
            item = (
                db.query(catalog)
                .filter(getattr(catalog, key_col) == str(key).strip())
                .first()
            )
            t = (
                item
                and db.query(tracker)
                .filter(
                    tracker.user_id == user.pk,
                    getattr(tracker, fk_col) == item.pk,
                )
                .first()
            )
            if not t:
                unmatched += 1
                continue
            if t.created_at == row['g_created']:
                same += 1
                continue
            # Bulk update on purpose: attribute assignment would fire the
            # base model's onupdate and re-date updated_at across thousands
            # of rows, making everything look freshly edited in Activity.
            db.query(tracker).filter(tracker.pk == t.pk).update(
                {
                    tracker.created_at: row['g_created'],
                    # Same onupdate pin as the episode fill above.
                    tracker.updated_at: tracker.updated_at,
                },
                synchronize_session=False,
            )
            fixed += 1
        print(f'{label:10} fixed={fixed:7} same={same:7} unmatched={unmatched:6}')


def run(dry_run: bool, email: str) -> None:
    """Execute the backfill (or report what it would do)."""
    orion_url = os.environ.get('ORION_MYSQL_URL')
    if not orion_url:
        sys.exit('ORION_MYSQL_URL is required (throwaway MySQL with the dump)')

    orion = create_engine(orion_url)
    db = SessionLocal()
    try:
        with orion.connect() as conn:
            user, orion_uid = _resolve_users(db, conn, email)
            print('--- episode watched_at ---')
            _backfill_episodes(db, conn, user, orion_uid)
            print('--- tracker created_at ---')
            _backfill_created_at(db, conn, user, orion_uid)
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
