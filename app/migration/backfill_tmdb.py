"""
Key the existing movie catalog onto TMDB and re-point posters off Amazon.

This is the bridge step of the OMDb->TMDB migration (#163). It must run
**after** the ``a1c4f80b6e37`` schema migration and **before** the TMDB search
cutover is user-visible: until every row has a ``tmdb`` id, search results
can't be matched against tracked movies (``tracked_status`` joins on ``tmdb``)
and the watchlist badges silently read as "not tracked".

For each movie it:

1. resolves ``imdb`` -> ``tmdb`` id via TMDB's ``/find`` endpoint,
2. re-points ``poster_url`` from ``m.media-amazon.com`` to image.tmdb.org,
3. fills ``rating_tmdb`` from TMDB's ``vote_average``.

Resumable: rows that already have a ``tmdb`` id are skipped, so a re-run
continues where an interrupted pass stopped. Unlike the OMDb-era
``enrich_movies``, there's no daily cap to nurse — TMDB publishes no daily
limit, only ~40-50 req/s — so the throttle exists purely to stay well clear
of that ceiling and of TMDB's "no bulk scraping" guidance.

Usage::

    TMDB_API_KEY=... DATABASE_URL=... ENV=prod \\
        python -m app.migration.backfill_tmdb --dry-run
    TMDB_API_KEY=... DATABASE_URL=... ENV=prod \\
        python -m app.migration.backfill_tmdb
"""

import argparse
import time

from app.db.database import SessionLocal
from app.db.models_sandbox import DbMovie
from app.services.movie_search import get_movie_detail, resolve_tmdb_id

# ~4 req/s against a ~40 req/s ceiling. A full pass over ~1,373 movies is
# roughly 6 minutes at two calls each (find + detail).
DEFAULT_THROTTLE_SECONDS = 0.25
# Hosts we're migrating away from; anything else (already-TMDB URLs, or the 7
# legacy image.tmdb.org posters) is left alone.
_LEGACY_POSTER_HOSTS = ('m.media-amazon.com', 'ia.media-imdb.com')


def _needs_new_poster(poster_url) -> bool:
    """
    True when TMDB's poster should replace what's stored: either a legacy
    hotlinked host we're migrating off, or nothing at all (a row with no
    poster renders a placeholder, so filling it is a pure gain). Posters
    already on image.tmdb.org are left alone.
    """
    if not poster_url:
        return True
    return any(h in poster_url for h in _LEGACY_POSTER_HOSTS)


def run(throttle: float = DEFAULT_THROTTLE_SECONDS, dry_run: bool = False) -> None:
    """Resolve TMDB ids and refresh posters for movies not yet keyed."""
    db = SessionLocal()
    try:
        pending = (
            db.query(DbMovie)
            .filter(DbMovie.tmdb.is_(None), DbMovie.imdb.isnot(None))
            .all()
        )
        total = len(pending)
        print(f'{total} movies to key onto TMDB' + (' (dry run)' if dry_run else ''))

        resolved = unresolved = reposted = 0
        unresolved_ids = []
        for processed, movie in enumerate(pending, start=1):
            tmdb_id = resolve_tmdb_id(movie.imdb)
            if not tmdb_id:
                unresolved += 1
                unresolved_ids.append(movie.imdb)
                time.sleep(throttle)
                continue

            resolved += 1
            detail = get_movie_detail(tmdb_id) or {}
            new_poster = detail.get('poster_url')

            if not dry_run:
                movie.tmdb = tmdb_id
                if detail.get('rating_tmdb') is not None:
                    movie.rating_tmdb = detail['rating_tmdb']
                # Only replace a poster we're actually migrating away from, and
                # only when TMDB gave us a real replacement — never blank out a
                # working image for a null.
                if new_poster and _needs_new_poster(movie.poster_url):
                    movie.poster_url = new_poster
                    reposted += 1
                db.commit()
            elif new_poster and _needs_new_poster(movie.poster_url):
                reposted += 1

            if processed % 25 == 0:
                print(
                    f'  {processed}/{total} '
                    f'(resolved {resolved}, unresolved {unresolved})'
                )
            time.sleep(throttle)

        print(
            f'Done: resolved {resolved}, unresolved {unresolved}, '
            f'posters re-pointed {reposted}'
        )
        if unresolved_ids:
            # These keep a NULL tmdb id: they stay in the catalog and still
            # render, but won't badge in search until fixed by hand.
            print('Unresolved imdb ids (left with NULL tmdb):')
            for imdb_id in unresolved_ids:
                print(f'  {imdb_id}')
    finally:
        db.close()


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--throttle',
        type=float,
        default=DEFAULT_THROTTLE_SECONDS,
        help=f'seconds between movies (default {DEFAULT_THROTTLE_SECONDS})',
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
