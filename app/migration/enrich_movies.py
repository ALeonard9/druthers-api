"""
Backfill TMDB detail (director/actors/genre/plot/year/rating) for movies that
were imported without it. Throttled and **resumable**: it only processes movies
still missing detail, so re-running continues where it left off.

Requires ``movies.tmdb`` to be populated — run
``app.migration.backfill_tmdb`` first (#163).

Usage::

    TMDB_API_KEY=... DATABASE_URL=... ENV=prod \\
        python -m app.migration.enrich_movies
"""

import time

from app.db.database import SessionLocal
from app.db.models_sandbox import DbMovie
from app.services.movie_search import apply_detail_to_movie, get_movie_detail

# ~4 req/s against TMDB's ~40 req/s ceiling. Far friendlier than the OMDb era,
# which needed a full second per call to survive a 1,000/day cap.
THROTTLE_SECONDS = 0.25
# get_movie_detail returns None both for genuine misses and for upstream
# trouble; a long run of consecutive Nones means something systemic (a revoked
# key, a network partition) rather than a catalog gap, so stop and let a re-run
# resume rather than burning through the rest of the list.
STOP_AFTER_CONSECUTIVE_MISSES = 15


def run() -> None:
    """Enrich all movies still missing detail."""
    db = SessionLocal()
    try:
        pending = (
            db.query(DbMovie)
            .filter(
                DbMovie.tmdb.isnot(None),
                DbMovie.plot.is_(None),
                DbMovie.director.is_(None),
            )
            .all()
        )
        total = len(pending)
        print(f'{total} movies to enrich')
        enriched = misses = consecutive = processed = 0
        for movie in pending:
            processed += 1
            detail = get_movie_detail(movie.tmdb)
            if detail:
                apply_detail_to_movie(movie, detail)
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
                    '(check TMDB_API_KEY and connectivity). Re-run to resume.'
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
