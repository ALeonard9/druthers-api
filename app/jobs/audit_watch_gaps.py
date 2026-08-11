"""
Find episodes that read as unwatched but almost certainly aren't (#169).

Two kinds of damage show up as a single stray unwatched episode in an
otherwise-finished season:

**Duplicate slots** — ``sync_episodes`` used to key only on the TVMaze
episode id. When TVMaze reassigned an id, the lookup missed and a second
``tv_episodes`` row was inserted for the same ``(season, season_number)``.
Watch history stayed on the original row, so the season grew a phantom
unwatched episode. Confirmed in prod on Firefly "Trash" (S1E12).

**Lone gaps** — a season where every episode but one is watched, with no
duplicate to explain it. Usually a genuine gap (the episode really wasn't
watched), sometimes an episode orion never had a row for at all. Reported,
never auto-fixed — only a human knows which.

``--fix`` repairs the first kind only: watch history is moved onto the
surviving row and the orphan duplicate is deleted. It never invents a watch.

Usage::

    DATABASE_URL=postgresql://... ENV=prod \\
        python -m app.jobs.audit_watch_gaps [--email adamleonard9@gmail.com]
        [--fix] [--max-gap 1]
"""

import argparse
from collections import defaultdict

from sqlalchemy import func

from app.db.database import SessionLocal
from app.db.models import DbUser
from app.db.models_sandbox import (
    DbTVEpisode,
    DbTVShow,
    DbUserTVEpisode,
    DbUserTVShow,
)


def _tracked_shows(db, user_pk):
    """Shows the user has on a list, newest tracker first."""
    return (
        db.query(DbTVShow)
        .join(DbUserTVShow, DbUserTVShow.tv_show_id == DbTVShow.pk)
        .filter(
            DbUserTVShow.user_id == user_pk,
            (DbUserTVShow.on_watchlist.is_(True))
            | (DbUserTVShow.on_rankings.is_(True)),
        )
        .all()
    )


def _watched_episode_pks(db, user_pk, show_pk):
    """Episode pks this user has marked watched for one show."""
    rows = (
        db.query(DbUserTVEpisode.episode_id)
        .join(DbTVEpisode, DbTVEpisode.pk == DbUserTVEpisode.episode_id)
        .filter(
            DbUserTVEpisode.user_id == user_pk,
            DbUserTVEpisode.watched == 1,
            DbTVEpisode.tv_show_id == show_pk,
        )
        .all()
    )
    return {pk for (pk,) in rows}


def find_duplicate_slots(db, show_pk):
    """
    ``(season, season_number) -> [episode rows]`` for slots holding more than
    one row — the signature of a TVMaze id reassignment.
    """
    dupe_slots = (
        db.query(DbTVEpisode.season, DbTVEpisode.season_number)
        .filter(DbTVEpisode.tv_show_id == show_pk)
        .group_by(DbTVEpisode.season, DbTVEpisode.season_number)
        .having(func.count() > 1)  # pylint: disable=not-callable
        .all()
    )
    out = {}
    for season, number in dupe_slots:
        out[(season, number)] = (
            db.query(DbTVEpisode)
            .filter(
                DbTVEpisode.tv_show_id == show_pk,
                DbTVEpisode.season == season,
                DbTVEpisode.season_number == number,
            )
            .order_by(DbTVEpisode.pk)
            .all()
        )
    return out


def _repair_slot(db, rows):
    """
    Collapse a duplicated slot onto its oldest row.

    The oldest row (lowest pk) is the original — the one carrying history.
    Every user's watch and favorite state on the newer rows is merged onto it
    before they're deleted, so a fix never loses another user's history.
    """
    keeper, orphans = rows[0], rows[1:]
    row_pks = [row.pk for row in rows]
    marks_by_user = defaultdict(list)
    for mark in (
        db.query(DbUserTVEpisode)
        .filter(DbUserTVEpisode.episode_id.in_(row_pks))
        .order_by(DbUserTVEpisode.pk)
        .all()
    ):
        marks_by_user[mark.user_id].append(mark)

    moved = False
    for marks in marks_by_user.values():
        keeper_mark = next(
            (mark for mark in marks if mark.episode_id == keeper.pk), None
        )
        orphan_marks = [mark for mark in marks if mark.episode_id != keeper.pk]

        if keeper_mark is None:
            keeper_mark = orphan_marks.pop(0)
            moved = moved or keeper_mark.watched == 1
            keeper_mark.episode_id = keeper.pk

        if any(mark.watched == 1 for mark in orphan_marks):
            moved = moved or keeper_mark.watched != 1
        watched_at = [
            mark.watched_at
            for mark in marks
            if mark.watched == 1 and mark.watched_at is not None
        ]
        favorited_at = [
            mark.favorited_at
            for mark in marks
            if mark.favorited and mark.favorited_at is not None
        ]
        keeper_mark.watched = int(any(mark.watched == 1 for mark in marks))
        keeper_mark.watched_at = min(watched_at) if watched_at else None
        keeper_mark.favorited = any(mark.favorited for mark in marks)
        keeper_mark.favorited_at = min(favorited_at) if favorited_at else None

        for mark in orphan_marks:
            db.delete(mark)

    # Persist tracker moves before deleting their old episode rows; otherwise
    # the relationship's delete-orphan cascade can remove a reassigned mark.
    db.flush()
    for orphan in orphans:
        db.delete(orphan)

    return keeper, len(orphans), moved


def _audit_duplicates(db, show, fix, findings):
    """Report or repair duplicated slots. Returns the slots it looked at."""
    dupes = find_duplicate_slots(db, show.pk)
    for slot, rows in dupes.items():
        label = f'{show.title} S{slot[0]}E{slot[1]}'
        if not fix:
            ids = ', '.join(f'pk={r.pk}/tvmaze={r.tvmaze}' for r in rows)
            findings['duplicates'].append(f'{label}: {ids}')
            continue
        keeper, removed, moved = _repair_slot(db, rows)
        # Flush so the rest of this pass (and the caller) queries against the
        # repaired state rather than the pending one.
        db.flush()
        findings['repaired'].append(
            f'{label}: kept pk={keeper.pk}, removed {removed} '
            f'duplicate row(s){", moved watch mark" if moved else ""}'
        )
    return dupes


def _audit_gaps(db, state, max_gap, findings):
    """Report seasons missing at most ``max_gap`` episodes."""
    show, watched, dupes = state
    # Unaired episodes have no airdate and must never read as a gap.
    episodes = (
        db.query(DbTVEpisode)
        .filter(
            DbTVEpisode.tv_show_id == show.pk,
            DbTVEpisode.airdate.isnot(None),
        )
        .all()
    )
    by_season = defaultdict(list)
    for ep in episodes:
        by_season[ep.season].append(ep)

    for season, eps in sorted(by_season.items(), key=lambda kv: kv[0] or 0):
        # Slots the duplicate pass already explained aren't independent gaps.
        missing = [
            e
            for e in eps
            if e.pk not in watched and (e.season, e.season_number) not in dupes
        ]
        if missing and len(missing) <= max_gap and len(eps) > len(missing):
            for ep in missing:
                findings['gaps'].append(
                    f'{show.title} S{season}E{ep.season_number} '
                    f'"{ep.title}" — {len(eps) - len(missing)}/{len(eps)} '
                    f'watched in this season'
                )


def audit(db, email=None, fix=False, max_gap=1):
    """Report (and optionally repair) stray unwatched episodes."""
    users = db.query(DbUser)
    if email:
        users = users.filter(DbUser.email == email)

    findings = defaultdict(list)
    for user in users.all():
        for show in _tracked_shows(db, user.pk):
            watched = _watched_episode_pks(db, user.pk, show.pk)
            if not watched:
                continue  # never started — not a gap
            dupes = _audit_duplicates(db, show, fix, findings)
            _audit_gaps(db, (show, watched, dupes), max_gap, findings)

    return findings


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--email', help='Audit a single user')
    parser.add_argument(
        '--fix', action='store_true', help='Repair duplicate slots (never gaps)'
    )
    parser.add_argument(
        '--max-gap',
        type=int,
        default=1,
        help='Report seasons missing at most this many episodes (default 1)',
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        findings = audit(db, email=args.email, fix=args.fix, max_gap=args.max_gap)
        for heading, key in (
            ('Duplicate slots (bug #169 — safe to --fix)', 'duplicates'),
            ('Repaired', 'repaired'),
            ('Lone unwatched episodes (review by hand)', 'gaps'),
        ):
            rows = findings.get(key)
            if not rows:
                continue
            print(f'\n{heading}: {len(rows)}')
            for row in rows:
                print(f'  {row}')

        if args.fix:
            db.commit()
            print('\nCommitted.')
        elif findings:
            print('\nRead-only. Re-run with --fix to collapse duplicate slots.')
        else:
            print('No stray episodes found.')
    finally:
        db.close()


if __name__ == '__main__':
    main()
