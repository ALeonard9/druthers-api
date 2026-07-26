"""
Backfill IGDB detail (year/genres/platforms/summary/rating/cover) for games
that were imported without it, keyed on the ``igdb`` id. Requires
``TWITCH_CLIENT_ID``/``TWITCH_CLIENT_SECRET``. Throttled and **resumable**:
it only processes games still missing detail, so re-running continues where
it left off.

Usage::

    TWITCH_CLIENT_ID=... TWITCH_CLIENT_SECRET=... \\
    DATABASE_URL=... ENV=prod python -m app.migration.enrich_games
"""

import time

from sqlalchemy import and_, or_

from app.db.database import SessionLocal
from app.db.models_sandbox import DbVideoGame
from app.services.game_search import apply_detail_to_game, get_game_detail

# IGDB allows 4 requests/second; stay well under it.
THROTTLE_SECONDS = 0.5
# get_game_detail returns None both for genuine misses and rate limiting; a
# run of consecutive Nones almost certainly means throttling or bad creds.
STOP_AFTER_CONSECUTIVE_MISSES = 15


def pending_games(db):
    """
    Games still worth an IGDB call.

    See ``enrich_movies.pending_movies``: ``summary``/``genre`` is only a
    proxy for "never enriched", so a row that has them but no ``year`` would
    never be retried. Select on the missing field too.
    """
    return (
        db.query(DbVideoGame)
        .filter(
            DbVideoGame.igdb.isnot(None),
            or_(
                and_(DbVideoGame.summary.is_(None), DbVideoGame.genre.is_(None)),
                DbVideoGame.year.is_(None),
            ),
        )
        .all()
    )


def run() -> None:
    """Enrich all games still missing detail."""
    db = SessionLocal()
    try:
        pending = pending_games(db)
        total = len(pending)
        print(f'{total} games to enrich')
        enriched = misses = consecutive = processed = 0
        for game in pending:
            processed += 1
            detail = get_game_detail(game.igdb)
            if detail:
                apply_detail_to_game(game, detail)
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
                    '(rate limit or missing Twitch creds). Re-run later to resume.'
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
