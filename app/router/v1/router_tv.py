# pylint: disable=missing-function-docstring, useless-return, too-many-lines
"""
This module contains the API routes for TV Shows and Episodes.

Mirrors the Movies pattern: admin-only global catalog CRUD, a TVMaze search
proxy, lazy enrichment on detail view, and per-user trackers with independent
Watchlist/Rankings lists plus episode-level watched marks.
"""

from datetime import datetime, time, timedelta
from typing import List, Optional, Union

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.auth.oauth2 import get_current_user, require_admin
from app.db.database import get_db
from app.db.models_sandbox import DbTVEpisode, DbTVShow, DbUserTVEpisode, DbUserTVShow
from app.schemas.schemas_sandbox import (
    ItemSocialContext,
    RankPlacement,
    ScheduleEpisodeItem,
    ScheduleFrozenShow,
    ScheduleResponse,
    TrackerListPage,
    TVEpisodeCreate,
    TVEpisodeResponse,
    TVEpisodeUpdate,
    TVRankingReorder,
    TVShowCreate,
    TVShowResponse,
    TVShowSearchResult,
    TVShowSummary,
    TVShowUpdate,
    UserTVEpisodeResponse,
    UserTVShowCreate,
    UserTVShowResponse,
    UserTVShowUpdate,
    UserTVShowWithStatus,
    WatchProviders,
)
from app.services import preferences
from app.services.rate_limit import catalog_add_cap, search_rate_limit
from app.services.search_correction import correct_query
from app.services.shelves import SHELVES
from app.services.social import get_item_social_context
from app.services.tracked_status import attach_tracked_status
from app.services.tracker_query import (
    list_params,
    list_tracker_items,
    tracker_list_response,
)
from app.services.tracker_rules import (
    default_completed_at,
    enforce_single_home,
    utc_now,
)
from app.services.tv_search import apply_detail_to_show, get_tv_show_detail
from app.services.tv_search import search_tv_shows as tvmaze_search_shows
from app.services.tv_search import sync_episodes
from app.services.watch_providers import DEFAULT_REGION, get_tv_providers

router = APIRouter(prefix='/v1', tags=['TV'])


def _local_day_start(user) -> datetime:
    """
    Midnight opening the caller's own today, tz-naive.

    Episode airdates are stored naive at midnight of a calendar date (see
    ``tv_search._to_date``), with no clock time to compare against -- so
    every "has this aired yet" question is really a calendar-day question.
    Anchoring the day on the user's ``time_zone`` (falling back to the
    deployment's ``TIME_ZONE``, #322) is what makes it turn over at their
    midnight instead of UTC's, which is up to a day's difference for a
    viewer in Sydney or Los Angeles.
    """
    today = datetime.now(preferences.time_zone_info(user.time_zone)).date()
    return datetime.combine(today, time.min)


def _local_day_end(user) -> datetime:
    """
    Midnight closing the caller's own today -- the "already aired" cutoff.

    Exclusive upper bound, so an episode dated today counts as aired (which
    is what the schedule has always done) without a stray clock time on the
    row pushing it into the future. Comparing against the *start* of today
    instead would quietly un-air anything that aired earlier the same day.
    """
    return _local_day_start(user) + timedelta(days=1)


# Global Entity Endpoints
@router.get('/tv-shows', response_model=List[TVShowSummary])
def get_all_tv_shows(  # pylint: disable=too-many-arguments, too-many-positional-arguments
    tvmaze: Optional[int] = None,
    imdb: Optional[str] = None,
    limit: int = Query(25, ge=1),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
):
    del current_user
    query = db.query(DbTVShow)
    if tvmaze is not None:
        query = query.filter(DbTVShow.tvmaze == tvmaze)
    if imdb is not None:
        query = query.filter(DbTVShow.imdb == imdb)
    return query.order_by(DbTVShow.pk).offset(offset).limit(limit).all()


@router.get(
    '/tv-shows/search',
    response_model=List[TVShowSearchResult],
    dependencies=[Depends(search_rate_limit)],
)
def search_tv_shows_endpoint(
    q: str,
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
):
    results = tvmaze_search_shows(q)
    if not results:
        corrected = correct_query(q)
        if corrected:
            results = tvmaze_search_shows(corrected)
    return attach_tracked_status(db, current_user[0].pk, results, 'tv_shows')


@router.post(
    '/tv-shows',
    response_model=TVShowResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(catalog_add_cap)],
)
def create_tv_show(
    request: TVShowCreate,
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
):
    # Any signed-in user may add to the shared catalog (the add-from-search
    # flow); editing and deleting catalog entries stay admin-only.
    del current_user
    if request.tvmaze:
        existing = db.query(DbTVShow).filter(DbTVShow.tvmaze == request.tvmaze).first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail='TV Show tvmaze id already exists',
            )
    if request.imdb:
        existing = db.query(DbTVShow).filter(DbTVShow.imdb == request.imdb).first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail='TV Show imdb already exists',
            )

    new_show = DbTVShow(**request.model_dump())
    # Enrich from TVMaze on add so detail/filtering work immediately, and pull
    # the episode list while we're at it (both best effort).
    detail = get_tv_show_detail(request.tvmaze)
    if detail:
        apply_detail_to_show(new_show, detail)
    db.add(new_show)
    db.flush()
    sync_episodes(db, new_show)
    db.commit()
    db.refresh(new_show)
    return new_show


@router.get('/tv-shows/{show_id}', response_model=TVShowResponse)
def get_tv_show(
    show_id: str,
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
):
    """Return one show's full detail, enriching from TVMaze on first view."""
    del current_user
    show = _get_show(db, show_id)
    # Lazily backfill detail + episodes the first time a sparse show is opened.
    if show.summary is None and show.premiered is None:
        detail = get_tv_show_detail(show.tvmaze)
        if detail:
            apply_detail_to_show(show, detail)
            sync_episodes(db, show)
            db.commit()
            db.refresh(show)
    return show


@router.get('/tv-shows/{show_id}/watch-providers', response_model=WatchProviders)
def get_tv_watch_providers(
    show_id: str,
    region: str = DEFAULT_REGION,
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
):
    """
    Where this show can be streamed, rented or bought in ``region`` (web#26).

    Keyed on the show's IMDb id, since the catalog's TV data comes from TVMaze
    and carries no TMDB id; a show without one returns empty buckets.
    """
    del current_user
    show = _get_show(db, show_id)
    return get_tv_providers(show.imdb, region)


@router.put('/tv-shows/{show_id}', response_model=TVShowResponse)
def update_tv_show(
    show_id: str,
    request: TVShowUpdate,
    db: Session = Depends(get_db),
    current_user: list = Depends(require_admin),
):
    del current_user
    show = _get_show(db, show_id)

    update_data = request.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(show, key, value)

    db.commit()
    db.refresh(show)
    return show


@router.delete('/tv-shows/{show_id}', status_code=status.HTTP_204_NO_CONTENT)
def delete_tv_show(
    show_id: str,
    db: Session = Depends(get_db),
    current_user: list = Depends(require_admin),
):
    del current_user
    show = _get_show(db, show_id)
    db.delete(show)
    db.commit()
    return None


# Episode Catalog Endpoints
@router.get('/tv-shows/{show_id}/episodes', response_model=List[TVEpisodeResponse])
def get_all_episodes(
    show_id: str,
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
):
    del current_user
    show = _get_show(db, show_id)
    return (
        db.query(DbTVEpisode)
        .filter(DbTVEpisode.tv_show_id == show.pk)
        .order_by(DbTVEpisode.season, DbTVEpisode.season_number)
        .all()
    )


@router.post(
    '/tv-shows/{show_id}/episodes/sync',
    response_model=List[TVEpisodeResponse],
)
def sync_show_episodes(
    show_id: str,
    db: Session = Depends(get_db),
    current_user: list = Depends(require_admin),
):
    """Refresh the episode list from TVMaze (for ongoing shows)."""
    del current_user
    show = _get_show(db, show_id)
    sync_episodes(db, show)
    db.commit()
    return (
        db.query(DbTVEpisode)
        .filter(DbTVEpisode.tv_show_id == show.pk)
        .order_by(DbTVEpisode.season, DbTVEpisode.season_number)
        .all()
    )


@router.post(
    '/tv-shows/{show_id}/episodes',
    response_model=TVEpisodeResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_episode(
    show_id: str,
    request: TVEpisodeCreate,
    db: Session = Depends(get_db),
    current_user: list = Depends(require_admin),
):
    del current_user
    show = _get_show(db, show_id)

    new_episode = DbTVEpisode(tv_show_id=show.pk, **request.model_dump())
    db.add(new_episode)
    db.commit()
    db.refresh(new_episode)
    return new_episode


@router.put('/episodes/{episode_id}', response_model=TVEpisodeResponse)
def update_episode(
    episode_id: str,
    request: TVEpisodeUpdate,
    db: Session = Depends(get_db),
    current_user: list = Depends(require_admin),
):
    del current_user
    episode = _get_episode(db, episode_id)

    update_data = request.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(episode, key, value)

    db.commit()
    db.refresh(episode)
    return episode


@router.delete('/episodes/{episode_id}', status_code=status.HTTP_204_NO_CONTENT)
def delete_episode(
    episode_id: str,
    db: Session = Depends(get_db),
    current_user: list = Depends(require_admin),
):
    del current_user
    episode = _get_episode(db, episode_id)
    db.delete(episode)
    db.commit()
    return None


# User Tracker Endpoints
def _get_show(db: Session, show_id: str) -> DbTVShow:
    show = db.query(DbTVShow).filter(DbTVShow.id == show_id).first()
    if not show:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail='TV Show not found'
        )
    return show


def _get_episode(db: Session, episode_id: str) -> DbTVEpisode:
    episode = db.query(DbTVEpisode).filter(DbTVEpisode.id == episode_id).first()
    if not episode:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail='Episode not found'
        )
    return episode


def _get_tracker(db: Session, user_pk: int, show_pk: int):
    return (
        db.query(DbUserTVShow)
        .filter(DbUserTVShow.user_id == user_pk, DbUserTVShow.tv_show_id == show_pk)
        .first()
    )


def _placed_count(db: Session, user_pk: int) -> int:
    """Number of shows with an assigned rank position for this user."""
    return (
        db.query(func.count())  # pylint: disable=not-callable
        .select_from(DbUserTVShow)
        .filter(
            DbUserTVShow.user_id == user_pk,
            DbUserTVShow.on_rankings.is_(True),
            DbUserTVShow.rank.isnot(None),
        )
        .scalar()
    )


def _close_rank_gap(db: Session, user_pk: int, vacated_rank) -> None:
    """After a ranked item leaves the list, shift everything below it up."""
    if vacated_rank is None:
        return
    db.query(DbUserTVShow).filter(
        DbUserTVShow.user_id == user_pk,
        DbUserTVShow.on_rankings.is_(True),
        DbUserTVShow.rank.isnot(None),
        DbUserTVShow.rank > vacated_rank,
    ).update({DbUserTVShow.rank: DbUserTVShow.rank - 1}, synchronize_session=False)


def _watch_status(aired: int, watched: int, show_status: Optional[str]) -> str:
    """The per-show badge the legacy site showed next to each series."""
    if aired == 0 or watched == 0:
        return 'not_started'
    if watched < aired:
        return 'behind'
    return 'complete' if show_status == 'Ended' else 'up_to_date'


@router.get(
    '/users/me/tv-shows',
    response_model=Union[
        List[UserTVShowWithStatus], TrackerListPage[UserTVShowWithStatus]
    ],
)
def get_user_tv_shows(
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
    params: dict = Depends(list_params),
):
    user_pk = current_user[0].pk
    query = (
        db.query(DbUserTVShow)
        .options(joinedload(DbUserTVShow.tv_show))
        .filter(DbUserTVShow.user_id == user_pk)
    )
    trackers, total = list_tracker_items(query, DbUserTVShow, DbTVShow, params)
    show_pks = [t.tv_show_id for t in trackers]
    aired_before = _local_day_end(current_user[0])

    aired: dict = {}
    watched: dict = {}
    if show_pks:
        aired = dict(
            db.query(
                DbTVEpisode.tv_show_id,
                func.count(),  # pylint: disable=not-callable
            )
            .filter(
                DbTVEpisode.tv_show_id.in_(show_pks),
                DbTVEpisode.airdate.isnot(None),
                DbTVEpisode.airdate < aired_before,
            )
            .group_by(DbTVEpisode.tv_show_id)
            .all()
        )
        watched = dict(
            db.query(
                DbTVEpisode.tv_show_id,
                func.count(),  # pylint: disable=not-callable
            )
            .join(DbUserTVEpisode, DbUserTVEpisode.episode_id == DbTVEpisode.pk)
            .filter(
                DbTVEpisode.tv_show_id.in_(show_pks),
                DbTVEpisode.airdate.isnot(None),
                DbTVEpisode.airdate < aired_before,
                DbUserTVEpisode.user_id == user_pk,
                DbUserTVEpisode.watched == 1,
            )
            .group_by(DbTVEpisode.tv_show_id)
            .all()
        )

    results = []
    for tracker in trackers:
        aired_count = aired.get(tracker.tv_show_id, 0)
        watched_count = watched.get(tracker.tv_show_id, 0)
        results.append(
            UserTVShowWithStatus(
                **UserTVShowResponse.model_validate(tracker).model_dump(),
                watch_status=_watch_status(
                    aired_count, watched_count, tracker.tv_show.status
                ),
                aired_count=aired_count,
                watched_count=watched_count,
            )
        )
    return tracker_list_response(results, total, params, 'TV')


@router.get('/users/me/schedule', response_model=ScheduleResponse)
def get_schedule(  # pylint: disable=too-many-locals
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
    window_days: int = 5,
):
    """
    What to watch: unwatched episodes airing within +/- ``window_days`` of
    today, everything overdue and unwatched (catch-up), and shows the user
    has frozen (paused tracking on, so they're excluded from both).

    "Today" is the caller's own calendar day (their ``time_zone``
    preference, falling back to the deployment's ``TIME_ZONE``), so the
    window turns over at their midnight rather than the server's.
    """
    user_pk = current_user[0].pk
    today = _local_day_start(current_user[0])
    aired_before = _local_day_end(current_user[0])
    window_start = today - timedelta(days=window_days)
    window_end = today + timedelta(days=window_days)

    trackers = (
        db.query(DbUserTVShow)
        .options(joinedload(DbUserTVShow.tv_show))
        .filter(
            DbUserTVShow.user_id == user_pk,
            (DbUserTVShow.on_watchlist.is_(True))
            | (DbUserTVShow.on_rankings.is_(True)),
        )
        .all()
    )
    frozen_shows = [
        ScheduleFrozenShow(show_id=t.tv_show.id, show_title=t.tv_show.title)
        for t in trackers
        if t.freeze
    ]
    active_show_pks = [t.tv_show_id for t in trackers if not t.freeze]

    upcoming: List[ScheduleEpisodeItem] = []
    catch_up: List[ScheduleEpisodeItem] = []
    if active_show_pks:
        shows_by_pk = {
            s.pk: s for s in db.query(DbTVShow).filter(DbTVShow.pk.in_(active_show_pks))
        }
        # catch_up only needs airdate < aired_before; upcoming only needs
        # <= window_end (>= window_start is checked in Python below).
        # window_end >= aired_before for any window_days >= 1, so bounding the
        # fetch to airdate <= window_end covers both.
        # The unwatched anti-join is pushed into SQL too, instead of loading
        # every episode of every active show and filtering in Python - row
        # count no longer scales with the full episode catalog.
        watched_exists = (
            db.query(DbUserTVEpisode.pk)
            .filter(
                DbUserTVEpisode.episode_id == DbTVEpisode.pk,
                DbUserTVEpisode.user_id == user_pk,
                DbUserTVEpisode.watched == 1,
            )
            .exists()
        )
        episodes = (
            db.query(DbTVEpisode)
            .filter(
                DbTVEpisode.tv_show_id.in_(active_show_pks),
                DbTVEpisode.airdate.isnot(None),
                DbTVEpisode.airdate <= window_end,
                ~watched_exists,
            )
            .all()
        )
        for ep in episodes:
            show = shows_by_pk.get(ep.tv_show_id)
            if show is None:
                continue
            item = ScheduleEpisodeItem(
                show_id=show.id,
                show_title=show.title,
                episode_id=ep.id,
                episode_title=ep.title,
                season=ep.season,
                season_number=ep.season_number,
                airdate=ep.airdate,
            )
            if window_start <= ep.airdate <= window_end:
                upcoming.append(item)
            if ep.airdate < aired_before:
                catch_up.append(item)

        upcoming.sort(key=lambda i: (i.airdate, i.show_title, i.season_number or 0))
        catch_up.sort(key=lambda i: (i.show_title, i.season or 0, i.season_number or 0))

    return ScheduleResponse(
        upcoming=upcoming, catch_up=catch_up, frozen_shows=frozen_shows
    )


@router.put(
    '/users/me/tv-shows/rankings/order', response_model=List[UserTVShowResponse]
)
def reorder_rankings(
    request: TVRankingReorder,
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
):
    """Persist a new ranking order (drag-and-drop). Rank = position in the list."""
    user_pk = current_user[0].pk

    shows = db.query(DbTVShow).filter(DbTVShow.id.in_(request.show_ids)).all()
    show_pk_by_id = {s.id: s.pk for s in shows}

    if show_pk_by_id:
        trackers = (
            db.query(DbUserTVShow)
            .filter(
                DbUserTVShow.user_id == user_pk,
                DbUserTVShow.tv_show_id.in_(show_pk_by_id.values()),
            )
            .all()
        )
        tracker_by_show_pk = {t.tv_show_id: t for t in trackers}
    else:
        tracker_by_show_pk = {}

    for position, show_id in enumerate(request.show_ids, start=1):
        show_pk = show_pk_by_id.get(show_id)
        if not show_pk:
            continue
        tracker = tracker_by_show_pk.get(show_pk)
        if tracker:
            if tracker.rank != position:
                tracker.ranked_at = utc_now()
            tracker.rank = position
            tracker.on_rankings = True
            tracker.on_watchlist = False
    db.commit()
    return (
        db.query(DbUserTVShow)
        .filter(DbUserTVShow.user_id == user_pk, DbUserTVShow.on_rankings.is_(True))
        .order_by(DbUserTVShow.rank)
        .all()
    )


@router.get('/users/me/tv-shows/{show_id}', response_model=UserTVShowResponse)
def get_user_tv_show(
    show_id: str,
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
):
    """Return the current user's tracker for one show (404 if not tracked)."""
    show = _get_show(db, show_id)
    tracker = _get_tracker(db, current_user[0].pk, show.pk)
    if not tracker:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail='TV Show not marked'
        )
    return tracker


@router.put('/users/me/tv-shows/{show_id}/rank', response_model=UserTVShowResponse)
def set_show_rank(
    show_id: str,
    request: RankPlacement,
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
):
    """
    Place a show at an exact 1-based position in the ranked list, shifting the
    shows at and below that position down by one. Works for a not-yet-ranked
    show (jump it in) or an already-ranked one (move it).
    """
    user_pk = current_user[0].pk
    show = _get_show(db, show_id)
    tracker = _get_tracker(db, user_pk, show.pk)
    if not tracker:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail='TV Show not marked'
        )

    old_rank = tracker.rank
    was_on_rankings = tracker.on_rankings
    tracker.on_rankings = True
    tracker.on_watchlist = False
    default_completed_at(tracker, was_on_rankings)
    # Remove from its current slot first so the shift math excludes it.
    tracker.rank = None
    db.flush()
    if old_rank is not None:
        db.query(DbUserTVShow).filter(
            DbUserTVShow.user_id == user_pk,
            DbUserTVShow.on_rankings.is_(True),
            DbUserTVShow.rank.isnot(None),
            DbUserTVShow.rank > old_rank,
        ).update({DbUserTVShow.rank: DbUserTVShow.rank - 1}, synchronize_session=False)

    target = max(1, min(request.position, _placed_count(db, user_pk) + 1))
    db.query(DbUserTVShow).filter(
        DbUserTVShow.user_id == user_pk,
        DbUserTVShow.on_rankings.is_(True),
        DbUserTVShow.rank.isnot(None),
        DbUserTVShow.rank >= target,
    ).update({DbUserTVShow.rank: DbUserTVShow.rank + 1}, synchronize_session=False)

    tracker.rank = target
    tracker.ranked_at = utc_now()
    db.commit()
    db.refresh(tracker)
    return tracker


@router.post(
    '/users/me/tv-shows/{show_id}',
    response_model=UserTVShowResponse,
    status_code=status.HTTP_201_CREATED,
)
def mark_tv_show(
    show_id: str,
    request: UserTVShowCreate,
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
):
    """Add a show to the user's lists (idempotent - merges list membership)."""
    user_pk = current_user[0].pk
    show = _get_show(db, show_id)
    tracker = _get_tracker(db, user_pk, show.pk)
    data = request.model_dump(exclude_unset=True)

    if tracker is None:
        was_on_rankings = False
        old_rank = None
        tracker = DbUserTVShow(
            user_id=user_pk,
            tv_show_id=show.pk,
            on_watchlist=bool(data.get('on_watchlist', False)),
            on_rankings=bool(data.get('on_rankings', False)),
            notes=data.get('notes'),
            completed_at=data.get('completed_at'),
        )
        db.add(tracker)
    else:
        was_on_rankings = tracker.on_rankings
        old_rank = tracker.rank
        for key in ('on_watchlist', 'on_rankings', 'notes', 'completed_at'):
            if key in data:
                setattr(tracker, key, data[key])

    # A show only holds a rank while it's on the ranked list AND was already
    # placed. Entering Rankings (or leaving it) resets to unplaced so it lands
    # in the "to rank" bucket rather than at a stale/leftover position -
    # unless it's the first ranked show, which auto-places at #1 (#289).
    enforce_single_home(tracker, data)
    default_completed_at(tracker, was_on_rankings)
    entering_rankings = tracker.on_rankings and not was_on_rankings
    if entering_rankings and _placed_count(db, user_pk) == 0:
        tracker.rank = 1
        tracker.ranked_at = utc_now()
    elif not tracker.on_rankings or not was_on_rankings:
        tracker.rank = None
        tracker.ranked_at = None
    if old_rank is not None and tracker.rank is None:
        _close_rank_gap(db, user_pk, old_rank)
    db.commit()
    db.refresh(tracker)
    return tracker


@router.put('/users/me/tv-shows/{show_id}', response_model=UserTVShowResponse)
def update_user_tv_show(
    show_id: str,
    request: UserTVShowUpdate,
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
):
    """Update list membership, rank, or notes for a tracked show."""
    user_pk = current_user[0].pk
    show = _get_show(db, show_id)
    tracker = _get_tracker(db, user_pk, show.pk)
    if not tracker:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail='TV Show not marked'
        )

    old_rank = tracker.rank
    was_on_rankings = tracker.on_rankings
    data = request.model_dump(exclude_unset=True)
    for key, value in data.items():
        setattr(tracker, key, value)
    enforce_single_home(tracker, data)
    default_completed_at(tracker, was_on_rankings)

    # Entering Rankings (or leaving it) resets to unplaced so a stale/leftover
    # rank never places the show automatically; it lands in "to rank" instead.
    if not tracker.on_rankings or not was_on_rankings:
        tracker.rank = None
        tracker.ranked_at = None

    # A removed placement leaves a gap - shift everything below it up.
    if old_rank is not None and tracker.rank is None:
        _close_rank_gap(db, user_pk, old_rank)

    # If it's on neither list, drop the tracker entirely.
    if not tracker.on_watchlist and not tracker.on_rankings:
        response = UserTVShowResponse.model_validate(tracker)
        db.delete(tracker)
        db.commit()
        return response

    db.commit()
    db.refresh(tracker)
    return tracker


@router.delete('/users/me/tv-shows/{show_id}', status_code=status.HTTP_204_NO_CONTENT)
def unmark_tv_show(
    show_id: str,
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
):
    user_pk = current_user[0].pk
    show = _get_show(db, show_id)
    tracker = _get_tracker(db, user_pk, show.pk)
    if not tracker:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail='TV Show not marked'
        )
    _close_rank_gap(db, user_pk, tracker.rank)
    db.delete(tracker)
    db.commit()
    return None


# User Episode Tracker Endpoints
@router.get(
    '/users/me/tv-shows/{show_id}/episodes',
    response_model=List[UserTVEpisodeResponse],
)
def get_user_episodes(
    show_id: str,
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
):
    """The current user's episode watch marks for one show."""
    show = _get_show(db, show_id)
    return (
        db.query(DbUserTVEpisode)
        .join(DbTVEpisode, DbUserTVEpisode.episode_id == DbTVEpisode.pk)
        .filter(
            DbUserTVEpisode.user_id == current_user[0].pk,
            DbTVEpisode.tv_show_id == show.pk,
        )
        .all()
    )


@router.post(
    '/users/me/episodes/{episode_id}',
    response_model=UserTVEpisodeResponse,
    status_code=status.HTTP_201_CREATED,
)
def mark_episode_watched(
    episode_id: str,
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
):
    """Mark an episode watched (idempotent)."""
    user_pk = current_user[0].pk
    episode = _get_episode(db, episode_id)

    tracker = (
        db.query(DbUserTVEpisode)
        .filter(
            DbUserTVEpisode.user_id == user_pk,
            DbUserTVEpisode.episode_id == episode.pk,
        )
        .first()
    )
    if tracker is None:
        tracker = DbUserTVEpisode(
            user_id=user_pk, episode_id=episode.pk, watched=1, watched_at=utc_now()
        )
        db.add(tracker)
    else:
        tracker.watched = 1
        if tracker.watched_at is None:
            tracker.watched_at = utc_now()
    db.commit()
    db.refresh(tracker)
    return tracker


@router.post(
    '/users/me/tv-shows/{show_id}/episodes/watch-all',
    response_model=List[UserTVEpisodeResponse],
)
def mark_all_episodes_watched(
    show_id: str,
    season: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
):
    """
    Mark every episode of a show watched (idempotent), or with ``season``
    just that one season's episodes.
    """
    user_pk = current_user[0].pk
    show = _get_show(db, show_id)

    episode_query = db.query(DbTVEpisode).filter(DbTVEpisode.tv_show_id == show.pk)
    if season is not None:
        episode_query = episode_query.filter(DbTVEpisode.season == season)
    episodes = episode_query.all()
    if not episodes:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='No episodes match that show/season',
        )

    episode_pks = [e.pk for e in episodes]
    existing_by_episode = {
        tracker.episode_id: tracker
        for tracker in db.query(DbUserTVEpisode).filter(
            DbUserTVEpisode.user_id == user_pk,
            DbUserTVEpisode.episode_id.in_(episode_pks),
        )
    }
    for ep in episodes:
        tracker = existing_by_episode.get(ep.pk)
        if tracker is None:
            db.add(
                DbUserTVEpisode(
                    user_id=user_pk,
                    episode_id=ep.pk,
                    watched=1,
                    watched_at=utc_now(),
                )
            )
        else:
            tracker.watched = 1
            if tracker.watched_at is None:
                tracker.watched_at = utc_now()
    db.commit()

    return (
        db.query(DbUserTVEpisode)
        .join(DbTVEpisode, DbUserTVEpisode.episode_id == DbTVEpisode.pk)
        .filter(
            DbUserTVEpisode.user_id == user_pk,
            DbUserTVEpisode.episode_id.in_(episode_pks),
        )
        .all()
    )


@router.delete(
    '/users/me/episodes/{episode_id}', status_code=status.HTTP_204_NO_CONTENT
)
def unmark_episode_watched(
    episode_id: str,
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
):
    user_pk = current_user[0].pk
    episode = _get_episode(db, episode_id)
    tracker = (
        db.query(DbUserTVEpisode)
        .filter(
            DbUserTVEpisode.user_id == user_pk,
            DbUserTVEpisode.episode_id == episode.pk,
        )
        .first()
    )
    if not tracker:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail='Episode not marked'
        )
    # A favorited episode keeps its row (and the favorite) even once
    # unwatched (#262) - only drop the row entirely once nothing's left on it.
    if tracker.favorited:
        tracker.watched = 0
        tracker.watched_at = None
        db.commit()
    else:
        db.delete(tracker)
        db.commit()
    return None


@router.post(
    '/users/me/episodes/{episode_id}/favorite',
    response_model=UserTVEpisodeResponse,
    status_code=status.HTTP_201_CREATED,
)
def mark_episode_favorited(
    episode_id: str,
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
):
    """Favorite an episode, independent of watched status (idempotent)."""
    user_pk = current_user[0].pk
    episode = _get_episode(db, episode_id)

    tracker = (
        db.query(DbUserTVEpisode)
        .filter(
            DbUserTVEpisode.user_id == user_pk,
            DbUserTVEpisode.episode_id == episode.pk,
        )
        .first()
    )
    if tracker is None:
        tracker = DbUserTVEpisode(
            user_id=user_pk,
            episode_id=episode.pk,
            favorited=True,
            favorited_at=utc_now(),
        )
        db.add(tracker)
    else:
        tracker.favorited = True
        if tracker.favorited_at is None:
            tracker.favorited_at = utc_now()
    db.commit()
    db.refresh(tracker)
    return tracker


@router.delete(
    '/users/me/episodes/{episode_id}/favorite', status_code=status.HTTP_204_NO_CONTENT
)
def unmark_episode_favorited(
    episode_id: str,
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
):
    user_pk = current_user[0].pk
    episode = _get_episode(db, episode_id)
    tracker = (
        db.query(DbUserTVEpisode)
        .filter(
            DbUserTVEpisode.user_id == user_pk,
            DbUserTVEpisode.episode_id == episode.pk,
        )
        .first()
    )
    if not tracker or not tracker.favorited:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail='Episode not favorited'
        )
    # Mirror unmark_episode_watched: only drop the row once nothing's left on it.
    if tracker.watched:
        tracker.favorited = False
        tracker.favorited_at = None
        db.commit()
    else:
        db.delete(tracker)
        db.commit()
    return None


@router.get('/tv/{tv_show_id}/social', response_model=list[ItemSocialContext])
def get_tv_social(
    tv_show_id: str,
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
):
    """Return social context (friends and followees) for one TV show."""
    shelf = next(s for s in SHELVES if s.category == 'tv')
    return get_item_social_context(db, current_user[0], shelf, tv_show_id)
