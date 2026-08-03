"""
Backfill IGDB "time to beat" (main story, whole hours) for games missing
it, keyed on the ``igdb`` id. Requires
``TWITCH_CLIENT_ID``/``TWITCH_CLIENT_SECRET``. Throttled and **resumable**:
it only processes games still missing a value, so re-running continues
where it left off, including for games added since the last run.

Usage::

    TWITCH_CLIENT_ID=... TWITCH_CLIENT_SECRET=... \\
    DATABASE_URL=... ENV=prod python -m app.migration.backfill_time_to_beat
"""

import time

from app.db.database import SessionLocal
from app.db.models_sandbox import DbVideoGame
from app.services.game_search import get_time_to_beat

# IGDB allows 4 requests/second; stay well under it.
THROTTLE_SECONDS = 0.5
# get_time_to_beat returns None both for genuine misses (no community data)
# and rate limiting; a run of consecutive Nones almost certainly means
# throttling or bad creds.
STOP_AFTER_CONSECUTIVE_MISSES = 15


def pending_games(db):
    """Games with an IGDB id but no time-to-beat value yet."""
    return (
        db.query(DbVideoGame)
        .filter(DbVideoGame.igdb.isnot(None), DbVideoGame.time_to_beat.is_(None))
        .all()
    )


def run() -> None:
    """Backfill time-to-beat for all games still missing it."""
    db = SessionLocal()
    try:
        pending = pending_games(db)
        total = len(pending)
        print(f'{total} games to backfill')
        filled = misses = consecutive = processed = 0
        for game in pending:
            processed += 1
            hours = get_time_to_beat(game.igdb)
            if hours is not None:
                game.time_to_beat = hours
                db.commit()
                filled += 1
                consecutive = 0
            else:
                misses += 1
                consecutive += 1
            if processed % 25 == 0:
                print(f'  {processed}/{total} (filled {filled}, misses {misses})')
            if consecutive >= STOP_AFTER_CONSECUTIVE_MISSES:
                print(
                    f'Stopping after {consecutive} consecutive misses '
                    '(rate limit or missing Twitch creds). Re-run later to resume.'
                )
                break
            time.sleep(THROTTLE_SECONDS)
        print(
            f'Done: filled {filled}, misses {misses}, '
            f'remaining ~{total - processed}'
        )
    finally:
        db.close()


if __name__ == '__main__':
    run()
