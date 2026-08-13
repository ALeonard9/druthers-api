# pylint: disable=missing-function-docstring, useless-return
"""
This module contains the API routes for Movies.
"""

from typing import List, Union

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload

from app.db.database import get_db
from app.services.rate_limit import catalog_add_cap, search_rate_limit
from app.services.tracker_rules import (
    default_completed_at,
    enforce_single_home,
    utc_now,
)
from app.db.models_sandbox import DbMovie, DbUserMovie
from app.auth.oauth2 import get_current_user, require_admin
from app.schemas.schemas_sandbox import (
    MovieCreate,
    MovieResponse,
    MovieSearchResult,
    MovieUpdate,
    RankPlacement,
    RankingReorder,
    TrackerListPage,
    UserMovieCreate,
    UserMovieResponse,
    UserMovieUpdate,
    WatchProviders,
)
from app.services.movie_search import (
    apply_detail_to_movie,
    get_movie_detail,
    resolve_tmdb_id,
    search_movies as tmdb_search_movies,
)
from app.services.watch_providers import DEFAULT_REGION, get_movie_providers
from app.services.search_correction import correct_query
from app.services.tracked_status import attach_tracked_status
from app.services.tracker_query import (
    list_tracker_items,
    list_params,
    tracker_list_response,
)

router = APIRouter(prefix='/v1', tags=['Movies'])


def _find_duplicate(db: Session, tmdb_id, imdb_id):
    """
    Existing catalog row matching either external id. Both are checked because
    a movie added by imdb before the TMDB migration and the same movie found
    via TMDB search must not become two rows.
    """
    clauses = []
    if tmdb_id:
        clauses.append(DbMovie.tmdb == tmdb_id)
    if imdb_id:
        clauses.append(DbMovie.imdb == imdb_id)
    if not clauses:
        return None
    return db.query(DbMovie).filter(or_(*clauses)).first()


# Global Entity Endpoints
@router.get('/movies', response_model=List[MovieResponse])
def get_all_movies(db: Session = Depends(get_db)):
    return db.query(DbMovie).all()


@router.get(
    '/movies/search',
    response_model=List[MovieSearchResult],
    dependencies=[Depends(search_rate_limit)],
)
def search_movies_endpoint(
    q: str,
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
):
    results = tmdb_search_movies(q)
    if not results:
        corrected = correct_query(q)
        if corrected:
            results = tmdb_search_movies(corrected)
    return attach_tracked_status(db, current_user[0].pk, results, 'movies')


@router.post(
    '/movies',
    response_model=MovieResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(catalog_add_cap)],
)
def create_movie(
    request: MovieCreate,
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
):
    # Any signed-in user may add to the shared catalog (the add-from-search
    # flow); editing and deleting catalog entries stay admin-only.
    del current_user
    # Either id is accepted: the search flow posts a tmdb id (TMDB search
    # returns no imdb), while the MCP tool and the IMDb CSV import (#140) are
    # imdb-driven. Whichever is missing gets filled in by enrichment below.
    if not request.tmdb and not request.imdb:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Movie requires a tmdb or imdb id',
        )

    tmdb_id = request.tmdb or resolve_tmdb_id(request.imdb)
    if _find_duplicate(db, tmdb_id, request.imdb):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail='Movie already exists'
        )

    new_movie = DbMovie(**request.model_dump())
    new_movie.tmdb = tmdb_id
    # Enrich from TMDB on add so detail/filtering work immediately (best
    # effort). This is also where imdb gets populated for search-driven adds.
    detail = get_movie_detail(tmdb_id)
    if detail:
        apply_detail_to_movie(new_movie, detail)
    db.add(new_movie)
    db.commit()
    db.refresh(new_movie)
    return new_movie


@router.get('/movies/{movie_id}', response_model=MovieResponse)
def get_movie(
    movie_id: str,
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
):
    """Return one movie's full detail, enriching from TMDB on first view."""
    del current_user
    movie = _get_movie(db, movie_id)
    # Lazily backfill detail the first time a sparse movie is opened. Rows the
    # backfill couldn't resolve have no tmdb id; get_movie_detail returns None
    # for those and the movie is served as-is.
    if movie.plot is None and movie.director is None:
        detail = get_movie_detail(movie.tmdb)
        if detail:
            apply_detail_to_movie(movie, detail)
            db.commit()
            db.refresh(movie)
    return movie


@router.get('/movies/{movie_id}/watch-providers', response_model=WatchProviders)
def get_movie_watch_providers(
    movie_id: str,
    region: str = DEFAULT_REGION,
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
):
    """
    Where this movie can be streamed, rented or bought in ``region`` (web#26).

    Live TMDB/JustWatch data, not catalog data, so nothing here is stored. A
    movie with no availability — or one the backfill never keyed onto TMDB —
    returns empty buckets rather than an error; only an unknown movie 404s.
    """
    del current_user
    movie = _get_movie(db, movie_id)
    return get_movie_providers(movie.tmdb, region)


@router.put('/movies/{movie_id}', response_model=MovieResponse)
def update_movie(
    movie_id: str,
    request: MovieUpdate,
    db: Session = Depends(get_db),
    current_user: list = Depends(require_admin),
):
    del current_user
    movie = db.query(DbMovie).filter(DbMovie.id == movie_id).first()
    if not movie:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail='Movie not found'
        )

    update_data = request.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(movie, key, value)

    db.commit()
    db.refresh(movie)
    return movie


@router.delete('/movies/{movie_id}', status_code=status.HTTP_204_NO_CONTENT)
def delete_movie(
    movie_id: str,
    db: Session = Depends(get_db),
    current_user: list = Depends(require_admin),
):
    del current_user
    movie = db.query(DbMovie).filter(DbMovie.id == movie_id).first()
    if not movie:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail='Movie not found'
        )
    db.delete(movie)
    db.commit()
    return None


# User Tracker Endpoints
def _get_movie(db: Session, movie_id: str) -> DbMovie:
    movie = db.query(DbMovie).filter(DbMovie.id == movie_id).first()
    if not movie:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail='Movie not found'
        )
    return movie


def _get_tracker(db: Session, user_pk: int, movie_pk: int):
    return (
        db.query(DbUserMovie)
        .filter(DbUserMovie.user_id == user_pk, DbUserMovie.movie_id == movie_pk)
        .first()
    )


def _placed_count(db: Session, user_pk: int) -> int:
    """Number of movies with an assigned rank position for this user."""
    return (
        db.query(func.count())  # pylint: disable=not-callable
        .select_from(DbUserMovie)
        .filter(
            DbUserMovie.user_id == user_pk,
            DbUserMovie.on_rankings.is_(True),
            DbUserMovie.rank.isnot(None),
        )
        .scalar()
    )


def _close_rank_gap(db: Session, user_pk: int, vacated_rank) -> None:
    """After a ranked item leaves the list, shift everything below it up."""
    if vacated_rank is None:
        return
    db.query(DbUserMovie).filter(
        DbUserMovie.user_id == user_pk,
        DbUserMovie.on_rankings.is_(True),
        DbUserMovie.rank.isnot(None),
        DbUserMovie.rank > vacated_rank,
    ).update({DbUserMovie.rank: DbUserMovie.rank - 1}, synchronize_session=False)


@router.get(
    '/users/me/movies',
    response_model=Union[List[UserMovieResponse], TrackerListPage[UserMovieResponse]],
)
def get_user_movies(
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
    params: dict = Depends(list_params),
):
    query = (
        db.query(DbUserMovie)
        .options(joinedload(DbUserMovie.movie))
        .filter(DbUserMovie.user_id == current_user[0].pk)
    )
    rows, total = list_tracker_items(query, DbUserMovie, DbMovie, params)
    return tracker_list_response(rows, total, params, 'Movie')


@router.get('/users/me/movies/{movie_id}', response_model=UserMovieResponse)
def get_user_movie(
    movie_id: str,
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
):
    """Return the current user's tracker for one movie (404 if not tracked)."""
    movie = _get_movie(db, movie_id)
    tracker = _get_tracker(db, current_user[0].pk, movie.pk)
    if not tracker:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail='Movie not marked'
        )
    return tracker


@router.put('/users/me/movies/rankings/order', response_model=List[UserMovieResponse])
def reorder_rankings(
    request: RankingReorder,
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
):
    """Persist a new ranking order (drag-and-drop). Rank = position in the list."""
    user_pk = current_user[0].pk
    for position, movie_id in enumerate(request.movie_ids, start=1):
        movie = db.query(DbMovie).filter(DbMovie.id == movie_id).first()
        if not movie:
            continue
        tracker = _get_tracker(db, user_pk, movie.pk)
        if tracker:
            if tracker.rank != position:
                tracker.ranked_at = utc_now()
            tracker.rank = position
            tracker.on_rankings = True
            tracker.on_watchlist = False
    db.commit()
    return (
        db.query(DbUserMovie)
        .filter(DbUserMovie.user_id == user_pk, DbUserMovie.on_rankings.is_(True))
        .order_by(DbUserMovie.rank)
        .all()
    )


@router.put('/users/me/movies/{movie_id}/rank', response_model=UserMovieResponse)
def set_movie_rank(
    movie_id: str,
    request: RankPlacement,
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
):
    """
    Place a movie at an exact 1-based position in the ranked list, shifting the
    movies at and below that position down by one. Works for a not-yet-ranked
    movie (jump it in) or an already-ranked one (move it).
    """
    user_pk = current_user[0].pk
    movie = _get_movie(db, movie_id)
    tracker = _get_tracker(db, user_pk, movie.pk)
    if not tracker:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail='Movie not marked'
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
        db.query(DbUserMovie).filter(
            DbUserMovie.user_id == user_pk,
            DbUserMovie.on_rankings.is_(True),
            DbUserMovie.rank.isnot(None),
            DbUserMovie.rank > old_rank,
        ).update({DbUserMovie.rank: DbUserMovie.rank - 1}, synchronize_session=False)

    target = max(1, min(request.position, _placed_count(db, user_pk) + 1))
    db.query(DbUserMovie).filter(
        DbUserMovie.user_id == user_pk,
        DbUserMovie.on_rankings.is_(True),
        DbUserMovie.rank.isnot(None),
        DbUserMovie.rank >= target,
    ).update({DbUserMovie.rank: DbUserMovie.rank + 1}, synchronize_session=False)

    tracker.rank = target
    tracker.ranked_at = utc_now()
    db.commit()
    db.refresh(tracker)
    return tracker


@router.post(
    '/users/me/movies/{movie_id}',
    response_model=UserMovieResponse,
    status_code=status.HTTP_201_CREATED,
)
def mark_movie(
    movie_id: str,
    request: UserMovieCreate,
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
):
    """Add a movie to the user's lists (idempotent — merges list membership)."""
    user_pk = current_user[0].pk
    movie = _get_movie(db, movie_id)
    tracker = _get_tracker(db, user_pk, movie.pk)
    data = request.model_dump(exclude_unset=True)

    if tracker is None:
        was_on_rankings = False
        old_rank = None
        tracker = DbUserMovie(
            user_id=user_pk,
            movie_id=movie.pk,
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

    # A movie only holds a rank while it's on the ranked list AND was already
    # placed. Entering Rankings (or leaving it) resets to unplaced so it lands
    # in the "to rank" bucket rather than at a stale/leftover position —
    # unless it's the first ranked movie, which auto-places at #1 (#289).
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


@router.put('/users/me/movies/{movie_id}', response_model=UserMovieResponse)
def update_user_movie(
    movie_id: str,
    request: UserMovieUpdate,
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
):
    """Update list membership, rank, or notes for a tracked movie."""
    user_pk = current_user[0].pk
    movie = _get_movie(db, movie_id)
    tracker = _get_tracker(db, user_pk, movie.pk)
    if not tracker:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail='Movie not marked'
        )

    old_rank = tracker.rank
    was_on_rankings = tracker.on_rankings
    data = request.model_dump(exclude_unset=True)
    for key, value in data.items():
        setattr(tracker, key, value)
    enforce_single_home(tracker, data)
    default_completed_at(tracker, was_on_rankings)

    # Entering Rankings (or leaving it) resets to unplaced so a stale/leftover
    # rank never places the movie automatically; it lands in "to rank" instead.
    if not tracker.on_rankings or not was_on_rankings:
        tracker.rank = None
        tracker.ranked_at = None

    # A removed placement leaves a gap — shift everything below it up.
    if old_rank is not None and tracker.rank is None:
        _close_rank_gap(db, user_pk, old_rank)

    # If it's on neither list, drop the tracker entirely.
    if not tracker.on_watchlist and not tracker.on_rankings:
        response = UserMovieResponse.model_validate(tracker)
        db.delete(tracker)
        db.commit()
        return response

    db.commit()
    db.refresh(tracker)
    return tracker


@router.delete('/users/me/movies/{movie_id}', status_code=status.HTTP_204_NO_CONTENT)
def unmark_movie(
    movie_id: str,
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
):
    user_pk = current_user[0].pk
    movie = _get_movie(db, movie_id)
    tracker = _get_tracker(db, user_pk, movie.pk)
    if not tracker:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail='Movie not marked'
        )
    _close_rank_gap(db, user_pk, tracker.rank)
    db.delete(tracker)
    db.commit()
    return None
