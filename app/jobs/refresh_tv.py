"""
Keep tracked TV shows current: pull newly aired episodes and refresh show
status from TVMaze.

This is the recurring counterpart to ``app.migration.enrich_tv``, which only
touches shows *missing* detail and therefore goes silent once the backfill is
done - it would never notice a new season. This job re-syncs shows a user
actually tracks, so the Schedule page keeps working.

Usage::

    DATABASE_URL=... ENV=prod python -m app.jobs.refresh_tv [--all] [--limit N]

Idempotent: ``sync_episodes`` upserts on the TVMaze episode id, so re-running
only ever adds new episodes and refreshes existing titles/airdates.

Each show is also reconciled for duplicated episode slots immediately after
its sync - see ``audit_watch_gaps.repair_duplicate_slots``. This job is the
only thing that creates them, so it is the only thing that has to clean them
up; ``app.jobs.audit_watch_gaps`` remains the by-hand entry point for
auditing gaps and for repairing a backlog that predates this.
"""

import argparse
import time

from sqlalchemy import or_

from app.db.database import SessionLocal
from app.db.models_sandbox import (
    DbTVShow,
    DbUserTVShow,
)
from app.jobs.audit_watch_gaps import repair_duplicate_slots
from app.log.logging_config import logger
from app.services.tv_search import (
    apply_detail_to_show,
    get_tv_show_detail,
    sync_episodes,
)

# TVMaze allows ~20 requests / 10s; each show costs two calls (detail +
# episodes), so 1.5s between shows keeps a full pass well inside the limit.
THROTTLE_SECONDS = 1.5
# Consecutive detail misses almost always mean we are being rate limited
# rather than that every remaining show vanished - stop and try again later.
STOP_AFTER_CONSECUTIVE_MISSES = 15
# Shows in these states can still gain episodes. Anything else is only worth
# refreshing on an explicit --all pass.
ACTIVE_STATUSES = ('Running', 'To Be Determined', 'In Development')


def _shows_to_refresh(db, include_ended: bool):
    """
    Shows tracked by at least one user, newest-airing first.

    Untracked catalog entries are skipped deliberately: they cost the same
    TVMaze budget but nothing in the product reads them.
    """
    query = (
        db.query(DbTVShow)
        .join(DbUserTVShow, DbUserTVShow.tv_show_id == DbTVShow.pk)
        .filter(DbTVShow.tvmaze.isnot(None))
    )
    if not include_ended:
        query = query.filter(
            or_(
                DbTVShow.status.in_(ACTIVE_STATUSES),
                DbTVShow.status.is_(None),
            )
        )
    return query.distinct().all()


def run(include_ended: bool = False, limit: int = 0) -> dict:
    """Refresh tracked shows. Returns a small report dict."""
    db = SessionLocal()
    report = {
        'shows': 0,
        'episodes_created': 0,
        'detail_updated': 0,
        'misses': 0,
        'slots_repaired': 0,
    }
    try:
        shows = _shows_to_refresh(db, include_ended)
        if limit:
            shows = shows[:limit]
        logger.info('refresh_tv: %d shows to refresh', len(shows))

        consecutive_misses = 0
        for show in shows:
            detail = get_tv_show_detail(show.tvmaze)
            if detail:
                apply_detail_to_show(show, detail)
                report['detail_updated'] += 1
                consecutive_misses = 0
            else:
                report['misses'] += 1
                consecutive_misses += 1
                if consecutive_misses >= STOP_AFTER_CONSECUTIVE_MISSES:
                    logger.warning(
                        'refresh_tv: %d consecutive misses - stopping early '
                        '(likely rate limited)',
                        consecutive_misses,
                    )
                    break

            created = sync_episodes(db, show)
            report['episodes_created'] += created
            # sync_episodes just refused to merge any slot that was already
            # ambiguous, so this is where duplicates are born. Reconcile the
            # show now rather than leaving the slot for whoever next notices
            # the Schedule and the detail page disagreeing (#240, #169).
            for repair in repair_duplicate_slots(db, show):
                logger.info('refresh_tv: repaired duplicate slot - %s', repair)
                report['slots_repaired'] += 1
            report['shows'] += 1
            # Commit per show so an early stop still keeps completed work.
            db.commit()
            time.sleep(THROTTLE_SECONDS)
    finally:
        db.close()

    logger.info(
        'refresh_tv: shows=%(shows)d episodes_created=%(episodes_created)d '
        'detail_updated=%(detail_updated)d misses=%(misses)d '
        'slots_repaired=%(slots_repaired)d',
        report,
    )
    return report


def main() -> None:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--all',
        action='store_true',
        dest='include_ended',
        help='Also refresh ended/cancelled shows (default: active only).',
    )
    parser.add_argument(
        '--limit',
        type=int,
        default=0,
        help='Cap the number of shows processed (0 = no cap).',
    )
    args = parser.parse_args()
    report = run(include_ended=args.include_ended, limit=args.limit)
    print(
        'refresh_tv: shows={shows} episodes_created={episodes_created} '
        'detail_updated={detail_updated} misses={misses} '
        'slots_repaired={slots_repaired}'.format(**report)
    )


if __name__ == '__main__':
    main()
