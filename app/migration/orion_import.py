"""
One-off ETL: legacy ``orion`` (MySQL) -> ``druthers`` (PostgreSQL).

Reads the legacy read-only source (a mysqldump loaded into a throwaway MySQL,
or the live DB) and upserts users and the five tracker domains into the modern
schema. The import is **idempotent**: it upserts catalog rows on their natural
keys and tracker rows on ``(user, item)``, so it can be re-run safely.

Usage::

    ORION_MYSQL_URL=mysql+pymysql://root:root@127.0.0.1:13306/orion \\
    DATABASE_URL=postgresql://druthers:druthers@127.0.0.1:15432/druthers ENV=prod \\
        python -m app.migration.orion_import [--dry-run]

The target connection is the same one the application uses (resolved from
``DATABASE_URL`` / ``POSTGRES_*`` via app settings). Run ``alembic upgrade head``
against the target first so the schema exists.
"""

# Generic ETL helpers intentionally take the (session, model, columns, transform)
# tuple positionally; this is a self-contained one-off script.
# pylint: disable=too-many-arguments, too-many-positional-arguments

import argparse
import os
import sys
from dataclasses import dataclass, field
from typing import Callable, Dict, Optional

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.db.database import SessionLocal
from app.db.hash import Hash
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
from app.log.logging_config import logger


@dataclass
class Report:
    """Accumulates per-table insert/update/skip counts for reconciliation."""

    inserted: Dict[str, int] = field(default_factory=dict)
    updated: Dict[str, int] = field(default_factory=dict)
    skipped: Dict[str, int] = field(default_factory=dict)
    source_counts: Dict[str, int] = field(default_factory=dict)

    def bump(self, bucket: Dict[str, int], table: str) -> None:
        """Increment a counter for ``table`` in ``bucket``."""
        bucket[table] = bucket.get(table, 0) + 1

    def render(self) -> str:
        """Return a human-readable reconciliation table."""
        tables = sorted(
            set(self.source_counts)
            | set(self.inserted)
            | set(self.updated)
            | set(self.skipped)
        )
        width = max((len(t) for t in tables), default=10)
        lines = [
            f'{"table".ljust(width)}  {"source":>8}  {"insert":>8}'
            f'  {"update":>8}  {"skip":>8}'
        ]
        for tbl in tables:
            lines.append(
                f'{tbl.ljust(width)}  {self.source_counts.get(tbl, 0):>8}'
                f'  {self.inserted.get(tbl, 0):>8}  {self.updated.get(tbl, 0):>8}'
                f'  {self.skipped.get(tbl, 0):>8}'
            )
        return '\n'.join(lines)


def _source_url() -> str:
    url = os.getenv('ORION_MYSQL_URL')
    if not url:
        logger.error('ORION_MYSQL_URL is not set')
        sys.exit(2)
    return url


def _clean(value: Optional[str], limit: Optional[int] = None) -> Optional[str]:
    """Trim whitespace and optionally truncate to fit the target column."""
    if value is None:
        return None
    value = str(value).strip()
    if limit is not None and len(value) > limit:
        value = value[:limit]
    return value or None


def _upsert(
    session: Session,
    model,
    natural_filter: dict,
    values: dict,
    table: str,
    report: Report,
):
    """
    Upsert one row identified by ``natural_filter``.

    Returns the persisted instance so callers can wire up foreign keys.
    """
    instance = session.query(model).filter_by(**natural_filter).one_or_none()
    if instance is None:
        instance = model(**{**natural_filter, **values})
        session.add(instance)
        session.flush()
        report.bump(report.inserted, table)
    else:
        for key, val in values.items():
            setattr(instance, key, val)
        session.flush()
        report.bump(report.updated, table)
    return instance


def _migrate_users(src, session, report) -> Dict[int, int]:
    """Migrate users; return legacy_id -> target pk map."""
    mapping: Dict[int, int] = {}
    rows = src.execute(
        text(
            'SELECT id, email, display_name, user_group, password, '
            'created, updated FROM users'
        )
    ).mappings()
    for row in rows:
        report.bump(report.source_counts, 'users')
        email = _clean(row['email'])
        if not email:
            report.bump(report.skipped, 'users')
            continue
        # Legacy user_group is 'User'/'Admin'; the new RBAC checks lowercase.
        group = (row['user_group'] or 'user').strip().lower()
        # Legacy passwords are bcrypt (or NULL for Google-only accounts). Argon2
        # cannot verify bcrypt, so store an unusable placeholder; users re-auth
        # via Google or password reset. Never leave a verifiable legacy hash.
        password = Hash.hash_password(os.urandom(16).hex())
        user = _upsert(
            session,
            DbUser,
            {'email': email},
            {
                'display_name': _clean(row['display_name'], 30),
                'user_group': group if group in ('user', 'admin') else 'user',
                'password': password,
                'created_at': row['created'],
                'updated_at': row['updated'],
            },
            'users',
            report,
        )
        mapping[row['id']] = user.pk
    return mapping


def _migrate_catalog(
    src, session, report, table, model, columns, natural_key, transform
) -> Dict[int, int]:
    """
    Generic catalog migration.

    ``columns`` is the SELECT column list; ``transform`` maps a source row to
    (natural_filter, values). Returns legacy_id -> target pk.
    """
    mapping: Dict[int, int] = {}
    rows = src.execute(
        text(f'SELECT {columns} FROM {table}')  # nosec: fixed column/table names
    ).mappings()
    for row in rows:
        report.bump(report.source_counts, table)
        natural_filter, values = transform(row)
        if natural_filter.get(natural_key) in (None, ''):
            report.bump(report.skipped, table)
            continue
        instance = _upsert(session, model, natural_filter, values, table, report)
        mapping[row['id']] = instance.pk
    return mapping


def _migrate_tracker(
    src, session, report, table, model, columns, transform, valid: Callable
):
    """Generic gerund/tracker migration keyed on (user_id, item_id)."""
    rows = src.execute(
        text(f'SELECT {columns} FROM {table}')  # nosec: fixed column/table names
    ).mappings()
    for row in rows:
        report.bump(report.source_counts, table)
        natural_filter, values = transform(row)
        if not valid(natural_filter):
            report.bump(report.skipped, table)
            continue
        _upsert(session, model, natural_filter, values, table, report)


def run_import(dry_run: bool = False) -> Report:
    """Execute the full import inside one transaction."""
    report = Report()
    src_engine = create_engine(_source_url())
    session = SessionLocal()
    try:
        with src_engine.connect() as src:
            user_map = _migrate_users(src, session, report)

            movie_map = _migrate_catalog(
                src,
                session,
                report,
                'movies',
                DbMovie,
                'id, title, imdb, poster_url, release_date, runtime, '
                'language, rating_imdb, rated',
                'imdb',
                lambda r: (
                    {'imdb': _clean(r['imdb'], 40)},
                    {
                        'title': _clean(r['title'], 255) or 'Untitled',
                        'poster_url': _clean(r['poster_url'], 500),
                        'release_date': r['release_date'],
                        'runtime': r['runtime'],
                        'language': _clean(r['language'], 40),
                        'rating_imdb': r['rating_imdb'],
                        'rated': _clean(r['rated'], 11),
                    },
                ),
            )
            _migrate_tracker(
                src,
                session,
                report,
                'g_user_movies',
                DbUserMovie,
                'movies_id, user_id, `rank`, completed, notes, g_first, g_updated',
                lambda r: (
                    {
                        'user_id': user_map.get(r['user_id']),
                        'movie_id': movie_map.get(r['movies_id']),
                    },
                    {
                        # Legacy movie ranks are 0-based, like TV's below; the
                        # API contract is 1-based (reorder_rankings enumerates
                        # from 1). An unranked row keeps no position at all —
                        # it used to carry its raw 0-based legacy rank, which
                        # is both meaningless off the rankings list and a
                        # ck_user_movies_rank_1_based violation.
                        'rank': (
                            r['rank'] + 1
                            if r['completed'] == 1 and r['rank'] is not None
                            else None
                        ),
                        'completed': r['completed'],
                        'notes': r['notes'],
                        # Derive the two lists here too (not only in the alembic
                        # backfill) so importing into an already-migrated schema
                        # still lands rows on a list.
                        'on_rankings': r['completed'] == 1,
                        'on_watchlist': r['completed'] != 1,
                        'created_at': r['g_first'] or r['g_updated'],
                        'updated_at': r['g_updated'],
                    },
                ),
                lambda nf: nf['user_id'] and nf['movie_id'],
            )

            tv_map = _migrate_catalog(
                src,
                session,
                report,
                'tv',
                DbTVShow,
                'id, title, imdb, tvmaze, status, poster_url',
                'imdb',
                lambda r: (
                    {'imdb': _clean(r['imdb'], 254)},
                    {
                        'title': _clean(r['title'], 254) or 'Untitled',
                        'tvmaze': r['tvmaze'],
                        'status': _clean(r['status'], 254),
                        'poster_url': _clean(r['poster_url'], 254),
                    },
                ),
            )
            episode_map = _migrate_catalog(
                src,
                session,
                report,
                'tvepisodes',
                DbTVEpisode,
                'id, title, tvmaze, tv_id, airdate, season, season_number',
                'tvmaze',
                lambda r: (
                    {'tvmaze': r['tvmaze']},
                    {
                        'title': _clean(r['title'], 254) or 'Untitled',
                        'tv_show_id': tv_map.get(r['tv_id']),
                        'airdate': r['airdate'],
                        'season': r['season'],
                        'season_number': r['season_number'],
                    },
                ),
            )
            _migrate_tracker(
                src,
                session,
                report,
                'g_user_tv',
                DbUserTVShow,
                'tv_id, user_id, `rank`, status, freeze',
                lambda r: (
                    {
                        'user_id': user_map.get(r['user_id']),
                        'tv_show_id': tv_map.get(r['tv_id']),
                    },
                    {
                        # Legacy TV ranks are 0-based; the API expects 1-based
                        # (mirrors the alembic backfill for pre-migration rows).
                        'rank': r['rank'] + 1 if r['rank'] is not None else None,
                        'status': _clean(r['status'], 254),
                        'freeze': r['freeze'] or 0,
                        'on_rankings': r['rank'] is not None,
                        'on_watchlist': r['rank'] is None,
                    },
                ),
                lambda nf: nf['user_id'] and nf['tv_show_id'],
            )
            _migrate_tracker(
                src,
                session,
                report,
                'g_user_tvepisodes',
                DbUserTVEpisode,
                'tvepisode_id, user_id, watched, g_first',
                lambda r: (
                    {
                        'user_id': user_map.get(r['user_id']),
                        'episode_id': episode_map.get(r['tvepisode_id']),
                    },
                    {'watched': r['watched'] or 0},
                ),
                lambda nf: nf['user_id'] and nf['episode_id'],
            )

            game_map = _migrate_catalog(
                src,
                session,
                report,
                'videogames',
                DbVideoGame,
                'id, title, igdb, poster_url, release_date, rating, '
                'time_to_beat, igdb_last_update, slug',
                'igdb',
                lambda r: (
                    {'igdb': r['igdb']},
                    {
                        'title': _clean(r['title'], 255) or 'Untitled',
                        'poster_url': _clean(r['poster_url'], 100),
                        'release_date': r['release_date'],
                        'rating': r['rating'],
                        'time_to_beat': r['time_to_beat'],
                        'igdb_last_update': r['igdb_last_update'],
                        'slug': _clean(r['slug'], 255),
                    },
                ),
            )
            _migrate_tracker(
                src,
                session,
                report,
                'g_user_videogames',
                DbUserVideoGame,
                'videogames_id, user_id, `rank`, completed, notes, `100_percent`',
                lambda r: (
                    {
                        'user_id': user_map.get(r['user_id']),
                        'game_id': game_map.get(r['videogames_id']),
                    },
                    {
                        # Backlog games carry a meaningless rank-0 sentinel;
                        # only played (completed) games keep their 1-based rank.
                        'rank': r['rank'] if r['completed'] == 1 else None,
                        'completed': r['completed'],
                        'notes': _decode_blob(r['notes']),
                        'is_100_percent': bool(r['100_percent']),
                        'on_rankings': r['completed'] == 1,
                        'on_watchlist': r['completed'] != 1,
                    },
                ),
                lambda nf: nf['user_id'] and nf['game_id'],
            )

            book_map = _migrate_catalog(
                src,
                session,
                report,
                'books',
                DbBook,
                'id, title, isbn, googleid, poster_url',
                'googleid',
                lambda r: (
                    {'googleid': _clean(r['googleid'], 254) or _clean(r['title'])},
                    {
                        'title': _clean(r['title'], 254) or 'Untitled',
                        'isbn': _clean(r['isbn'], 20),
                        'poster_url': _clean(r['poster_url'], 254),
                    },
                ),
                # books has no reliable unique key; googleid (fallback: title).
            )
            _migrate_tracker(
                src,
                session,
                report,
                'g_user_books',
                DbUserBook,
                'books_id, user_id, `rank`, completed, notes',
                lambda r: (
                    {
                        'user_id': user_map.get(r['user_id']),
                        'book_id': book_map.get(r['books_id']),
                    },
                    {
                        # Unread books carry a meaningless rank-0 sentinel;
                        # only read (completed) books keep their 1-based rank.
                        'rank': r['rank'] if r['completed'] == 1 else None,
                        'completed': r['completed'],
                        'notes': r['notes'],
                        'on_rankings': r['completed'] == 1,
                        'on_watchlist': r['completed'] != 1,
                    },
                ),
                lambda nf: nf['user_id'] and nf['book_id'],
            )

        if dry_run:
            logger.info('Dry run: rolling back all changes')
            session.rollback()
        else:
            session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
        src_engine.dispose()
    return report


def _decode_blob(value) -> Optional[str]:
    """Decode a MySQL blob (bytes) column to text."""
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray)):
        return value.decode('utf-8', errors='replace')
    return str(value)


def main() -> None:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Run the full import in a transaction and roll back (no writes).',
    )
    args = parser.parse_args()
    report = run_import(dry_run=args.dry_run)
    print('\n=== orion -> druthers import reconciliation ===')
    print(report.render())
    if args.dry_run:
        print('\n(dry run - no changes were committed)')


if __name__ == '__main__':
    main()
