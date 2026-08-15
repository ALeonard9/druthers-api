"""
Test the recurring TV refresh job's show-selection and slot-repair rules.
"""

# The selection helper is the contract worth pinning here, private or not.
# pylint: disable=protected-access

from datetime import datetime
from unittest.mock import patch

from app.db.models_sandbox import (
    DbTVEpisode,
    DbTVShow,
    DbUserTVEpisode,
    DbUserTVShow,
)
from app.jobs import refresh_tv


def _show(db, title, tvmaze, status):
    show = DbTVShow(title=title, tvmaze=tvmaze, status=status)
    db.add(show)
    db.flush()
    return show


def test_only_tracked_shows_are_refreshed(test_db_session):
    """
    Untracked catalog entries cost TVMaze budget for nothing.
    """
    tracked = _show(test_db_session, 'Tracked', 101, 'Running')
    _show(test_db_session, 'Untracked', 102, 'Running')
    test_db_session.add(DbUserTVShow(user_id=1, tv_show_id=tracked.pk))
    test_db_session.flush()

    picked = refresh_tv._shows_to_refresh(test_db_session, include_ended=False)
    assert [s.title for s in picked] == ['Tracked']


def test_ended_shows_are_skipped_by_default_and_included_with_all(test_db_session):
    """
    Ended shows cannot gain episodes, so they are off the nightly path but
    reachable with --all.
    """
    ended = _show(test_db_session, 'Ended Show', 201, 'Ended')
    running = _show(test_db_session, 'Running Show', 202, 'Running')
    unknown = _show(test_db_session, 'Unknown Status', 203, None)
    for s in (ended, running, unknown):
        test_db_session.add(DbUserTVShow(user_id=1, tv_show_id=s.pk))
    test_db_session.flush()

    default = {s.title for s in refresh_tv._shows_to_refresh(test_db_session, False)}
    assert default == {'Running Show', 'Unknown Status'}

    every = {s.title for s in refresh_tv._shows_to_refresh(test_db_session, True)}
    assert every == {'Ended Show', 'Running Show', 'Unknown Status'}


def test_shows_without_a_tvmaze_id_are_skipped(test_db_session):
    """
    There is nothing to refresh against without an upstream id.
    """
    show = _show(test_db_session, 'No TVMaze', None, 'Running')
    test_db_session.add(DbUserTVShow(user_id=1, tv_show_id=show.pk))
    test_db_session.flush()

    assert refresh_tv._shows_to_refresh(test_db_session, include_ended=False) == []


@patch('app.jobs.refresh_tv.sync_episodes', return_value=3)
@patch('app.jobs.refresh_tv.get_tv_show_detail', return_value=None)
@patch('app.jobs.refresh_tv.time.sleep')
def test_stops_early_after_consecutive_misses(_sleep, _detail, _sync, test_db_session):
    """
    A run of misses means rate limiting - stop rather than hammer TVMaze.
    """
    for i in range(refresh_tv.STOP_AFTER_CONSECUTIVE_MISSES + 5):
        show = _show(test_db_session, f'Show {i}', 300 + i, 'Running')
        test_db_session.add(DbUserTVShow(user_id=1, tv_show_id=show.pk))
    test_db_session.flush()

    with patch('app.jobs.refresh_tv.SessionLocal', return_value=test_db_session):
        report = refresh_tv.run()

    assert report['misses'] == refresh_tv.STOP_AFTER_CONSECUTIVE_MISSES
    # Stopped before touching every show
    assert report['shows'] < refresh_tv.STOP_AFTER_CONSECUTIVE_MISSES + 5


@patch('app.jobs.refresh_tv.sync_episodes', return_value=0)
@patch('app.jobs.refresh_tv.get_tv_show_detail', return_value=None)
@patch('app.jobs.refresh_tv.time.sleep')
def test_run_repairs_duplicate_slots_left_by_the_sync(
    _sleep, _detail, _sync, test_db_session
):
    """
    The nightly sync is the only thing that creates ambiguous slots, so it has
    to be the thing that clears them - otherwise the Schedule and the detail
    page disagree until someone runs the CLI by hand (#240).
    """
    show = _show(test_db_session, 'Reassigned', 401, 'Running')
    test_db_session.add(DbUserTVShow(user_id=1, tv_show_id=show.pk))
    aired = datetime(2026, 8, 1)
    keeper = DbTVEpisode(
        title='Keeper',
        tvmaze=4010,
        tv_show_id=show.pk,
        airdate=aired,
        season=1,
        season_number=1,
    )
    newer = DbTVEpisode(
        title='Reassigned id',
        tvmaze=4011,
        tv_show_id=show.pk,
        airdate=aired,
        season=1,
        season_number=1,
    )
    test_db_session.add_all([keeper, newer])
    test_db_session.flush()
    # Two users, state split across both rows - the case where a user-scoped
    # repair would drop whoever it was not looking at.
    test_db_session.add_all(
        [
            DbUserTVEpisode(episode_id=keeper.pk, user_id=1, watched=0, favorited=True),
            DbUserTVEpisode(episode_id=newer.pk, user_id=1, watched=1, favorited=False),
            DbUserTVEpisode(episode_id=newer.pk, user_id=2, watched=1, favorited=True),
        ]
    )
    test_db_session.flush()
    # run() closes the session, detaching these - keep the ids as plain ints.
    show_pk, keeper_pk = show.pk, keeper.pk

    with patch('app.jobs.refresh_tv.SessionLocal', return_value=test_db_session):
        report = refresh_tv.run()

    assert report['slots_repaired'] == 1
    survivors = (
        test_db_session.query(DbTVEpisode)
        .filter(DbTVEpisode.tv_show_id == show_pk)
        .all()
    )
    assert [e.pk for e in survivors] == [keeper_pk]
    marks = {
        m.user_id: m
        for m in test_db_session.query(DbUserTVEpisode)
        .filter(DbUserTVEpisode.episode_id == keeper_pk)
        .all()
    }
    # Both users keep watch *and* favorite, merged onto the surviving row.
    assert set(marks) == {1, 2}
    assert marks[1].watched == 1 and marks[1].favorited is True
    assert marks[2].watched == 1 and marks[2].favorited is True


@patch('app.jobs.refresh_tv.sync_episodes', return_value=0)
@patch('app.jobs.refresh_tv.get_tv_show_detail', return_value=None)
@patch('app.jobs.refresh_tv.time.sleep')
def test_run_leaves_clean_shows_alone(_sleep, _detail, _sync, test_db_session):
    """
    A show with no duplicated slot must not report a repair - otherwise the
    nightly log cries wolf and nobody reads it.
    """
    show = _show(test_db_session, 'Clean', 402, 'Running')
    test_db_session.add(DbUserTVShow(user_id=1, tv_show_id=show.pk))
    test_db_session.add(
        DbTVEpisode(
            title='Only',
            tvmaze=4020,
            tv_show_id=show.pk,
            airdate=datetime(2026, 8, 1),
            season=1,
            season_number=1,
        )
    )
    test_db_session.flush()

    with patch('app.jobs.refresh_tv.SessionLocal', return_value=test_db_session):
        report = refresh_tv.run()

    assert report['slots_repaired'] == 0
    assert test_db_session.query(DbTVEpisode).count() == 1
