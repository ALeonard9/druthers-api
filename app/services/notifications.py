"""
Notification generators.

Each generator sweeps one event source and upserts ``DbNotification`` rows
keyed on ``dedupe_key``, so sweeps are idempotent and safe to run on every
fetch. Today they run lazily when a client reads its notifications; a future
push channel (mobile, Telegram) can call the same functions from a cron and
then deliver whatever rows come back unread.
"""

from datetime import datetime, timedelta, timezone
from typing import List

from sqlalchemy.orm import Session

from app.db.models_sandbox import DbMovie, DbNotification, DbUserMovie

RELEASE_WINDOW_DAYS = 7


def _existing_keys(db: Session, user_pk: int, keys: List[str]) -> set:
    if not keys:
        return set()
    rows = db.query(DbNotification.dedupe_key).filter(
        DbNotification.user_id == user_pk,
        DbNotification.dedupe_key.in_(keys),
    )
    return {row.dedupe_key for row in rows}


def sweep_movie_releases(db: Session, user_pk: int) -> int:
    """
    Notify about watchlist movies whose release date lands within the next
    ``RELEASE_WINDOW_DAYS`` days. Returns the number of notifications created.
    """
    # release_date is stored tz-naive (parsed from TMDB), so compare naive.
    today = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0, tzinfo=None
    )
    window_end = today + timedelta(days=RELEASE_WINDOW_DAYS)

    trackers = (
        db.query(DbUserMovie)
        .join(DbMovie, DbUserMovie.movie_id == DbMovie.pk)
        .filter(
            DbUserMovie.user_id == user_pk,
            DbUserMovie.on_watchlist.is_(True),
            DbMovie.release_date.isnot(None),
            DbMovie.release_date >= today,
            DbMovie.release_date <= window_end,
        )
        .all()
    )
    if not trackers:
        return 0

    keys = [f'movie_release:{t.movie.id}' for t in trackers]
    existing = _existing_keys(db, user_pk, keys)
    created = 0
    for tracker in trackers:
        movie = tracker.movie
        key = f'movie_release:{movie.id}'
        if key in existing:
            continue
        # %-d is glibc-only (the container is musl/alpine), so build manually.
        release_day = f'{movie.release_date.strftime("%B")} {movie.release_date.day}'
        db.add(
            DbNotification(
                user_id=user_pk,
                type='movie_release',
                title=f'{movie.title} hits theaters soon',
                body=f'{movie.title} releases {release_day} — it\'s on your watchlist.',
                category='movie',
                entity_id=movie.id,
                dedupe_key=key,
            )
        )
        created += 1
    return created


def sweep_all(db: Session, user_pk: int) -> int:
    """Run every generator for one user. Commits if anything was created."""
    created = sweep_movie_releases(db, user_pk)
    if created:
        db.commit()
    return created
