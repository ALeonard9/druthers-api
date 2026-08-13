# pylint: disable=missing-function-docstring, useless-return
"""
This module contains the API routes for Video Games.

Mirrors the Movies pattern: admin-only global catalog CRUD, an IGDB search
proxy (Twitch OAuth; 503 when unconfigured), lazy enrichment on detail view,
and per-user trackers with independent Watchlist (backlog) / Rankings
(played) lists plus a 100%-completion flag.
"""

from typing import List, Union

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.db.database import get_db
from app.services.rate_limit import catalog_add_cap, search_rate_limit
from app.services.tracker_query import (
    list_tracker_items,
    list_params,
    tracker_list_response,
)
from app.services.tracker_rules import (
    default_completed_at,
    enforce_single_home,
    utc_now,
)
from app.db.models_sandbox import DbVideoGame, DbUserVideoGame
from app.auth.oauth2 import get_current_user, require_admin
from app.schemas.schemas_sandbox import (
    GameRankingReorder,
    GameSearchResult,
    RankPlacement,
    TrackerListPage,
    UserVideoGameCreate,
    UserVideoGameResponse,
    UserVideoGameUpdate,
    VideoGameCreate,
    VideoGameResponse,
    VideoGameSummary,
    VideoGameUpdate,
)
from app.services.game_search import (
    apply_detail_to_game,
    get_game_detail,
    search_games as igdb_search_games,
)
from app.services.search_correction import correct_query
from app.services.tracked_status import attach_tracked_status

router = APIRouter(prefix='/v1', tags=['Video Games'])


# Global Entity Endpoints
@router.get('/games', response_model=List[VideoGameSummary])
def get_all_games(db: Session = Depends(get_db)):
    return db.query(DbVideoGame).all()


@router.get(
    '/games/search',
    response_model=List[GameSearchResult],
    dependencies=[Depends(search_rate_limit)],
)
def search_games_endpoint(
    q: str,
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
):
    results = igdb_search_games(q)
    if not results:
        corrected = correct_query(q)
        if corrected:
            results = igdb_search_games(corrected)
    return attach_tracked_status(db, current_user[0].pk, results, 'games')


@router.post(
    '/games',
    response_model=VideoGameResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(catalog_add_cap)],
)
def create_game(
    request: VideoGameCreate,
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
):
    # Any signed-in user may add to the shared catalog (the add-from-search
    # flow); editing and deleting catalog entries stay admin-only.
    del current_user
    if request.igdb:
        existing = (
            db.query(DbVideoGame).filter(DbVideoGame.igdb == request.igdb).first()
        )
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail='Game igdb id already exists',
            )

    new_game = DbVideoGame(**request.model_dump())
    # Enrich from IGDB on add so detail/filtering work immediately (best effort).
    detail = get_game_detail(request.igdb)
    if detail:
        apply_detail_to_game(new_game, detail)
    db.add(new_game)
    db.commit()
    db.refresh(new_game)
    return new_game


@router.get('/games/{game_id}', response_model=VideoGameResponse)
def get_game(
    game_id: str,
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
):
    """Return one game's full detail, enriching from IGDB on first view."""
    del current_user
    game = _get_game(db, game_id)
    # Lazily backfill detail the first time a sparse game is opened.
    if game.summary is None and game.genre is None:
        detail = get_game_detail(game.igdb)
        if detail:
            apply_detail_to_game(game, detail)
            db.commit()
            db.refresh(game)
    return game


@router.put('/games/{game_id}', response_model=VideoGameResponse)
def update_game(
    game_id: str,
    request: VideoGameUpdate,
    db: Session = Depends(get_db),
    current_user: list = Depends(require_admin),
):
    del current_user
    game = _get_game(db, game_id)

    update_data = request.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(game, key, value)

    db.commit()
    db.refresh(game)
    return game


@router.delete('/games/{game_id}', status_code=status.HTTP_204_NO_CONTENT)
def delete_game(
    game_id: str,
    db: Session = Depends(get_db),
    current_user: list = Depends(require_admin),
):
    del current_user
    game = _get_game(db, game_id)
    db.delete(game)
    db.commit()
    return None


# User Tracker Endpoints
def _get_game(db: Session, game_id: str) -> DbVideoGame:
    game = db.query(DbVideoGame).filter(DbVideoGame.id == game_id).first()
    if not game:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail='Game not found'
        )
    return game


def _get_tracker(db: Session, user_pk: int, game_pk: int):
    return (
        db.query(DbUserVideoGame)
        .filter(DbUserVideoGame.user_id == user_pk, DbUserVideoGame.game_id == game_pk)
        .first()
    )


def _placed_count(db: Session, user_pk: int) -> int:
    """Number of games with an assigned rank position for this user."""
    return (
        db.query(func.count())  # pylint: disable=not-callable
        .select_from(DbUserVideoGame)
        .filter(
            DbUserVideoGame.user_id == user_pk,
            DbUserVideoGame.on_rankings.is_(True),
            DbUserVideoGame.rank.isnot(None),
        )
        .scalar()
    )


def _close_rank_gap(db: Session, user_pk: int, vacated_rank) -> None:
    """After a ranked item leaves the list, shift everything below it up."""
    if vacated_rank is None:
        return
    db.query(DbUserVideoGame).filter(
        DbUserVideoGame.user_id == user_pk,
        DbUserVideoGame.on_rankings.is_(True),
        DbUserVideoGame.rank.isnot(None),
        DbUserVideoGame.rank > vacated_rank,
    ).update(
        {DbUserVideoGame.rank: DbUserVideoGame.rank - 1}, synchronize_session=False
    )


@router.get(
    '/users/me/games',
    response_model=Union[
        List[UserVideoGameResponse], TrackerListPage[UserVideoGameResponse]
    ],
)
def get_user_games(
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
    params: dict = Depends(list_params),
):
    query = (
        db.query(DbUserVideoGame)
        .options(joinedload(DbUserVideoGame.game))
        .filter(DbUserVideoGame.user_id == current_user[0].pk)
    )
    rows, total = list_tracker_items(query, DbUserVideoGame, DbVideoGame, params)
    return tracker_list_response(rows, total, params, 'Game')


@router.put(
    '/users/me/games/rankings/order', response_model=List[UserVideoGameResponse]
)
def reorder_rankings(
    request: GameRankingReorder,
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
):
    """Persist a new ranking order (drag-and-drop). Rank = position in the list."""
    user_pk = current_user[0].pk
    for position, game_id in enumerate(request.game_ids, start=1):
        game = db.query(DbVideoGame).filter(DbVideoGame.id == game_id).first()
        if not game:
            continue
        tracker = _get_tracker(db, user_pk, game.pk)
        if tracker:
            if tracker.rank != position:
                tracker.ranked_at = utc_now()
            tracker.rank = position
            tracker.on_rankings = True
            tracker.on_watchlist = False
    db.commit()
    return (
        db.query(DbUserVideoGame)
        .filter(
            DbUserVideoGame.user_id == user_pk,
            DbUserVideoGame.on_rankings.is_(True),
        )
        .order_by(DbUserVideoGame.rank)
        .all()
    )


@router.get('/users/me/games/{game_id}', response_model=UserVideoGameResponse)
def get_user_game(
    game_id: str,
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
):
    """Return the current user's tracker for one game (404 if not tracked)."""
    game = _get_game(db, game_id)
    tracker = _get_tracker(db, current_user[0].pk, game.pk)
    if not tracker:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail='Game not marked'
        )
    return tracker


@router.put('/users/me/games/{game_id}/rank', response_model=UserVideoGameResponse)
def set_game_rank(
    game_id: str,
    request: RankPlacement,
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
):
    """
    Place a game at an exact 1-based position in the ranked list, shifting the
    games at and below that position down by one. Works for a not-yet-ranked
    game (jump it in) or an already-ranked one (move it).
    """
    user_pk = current_user[0].pk
    game = _get_game(db, game_id)
    tracker = _get_tracker(db, user_pk, game.pk)
    if not tracker:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail='Game not marked'
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
        db.query(DbUserVideoGame).filter(
            DbUserVideoGame.user_id == user_pk,
            DbUserVideoGame.on_rankings.is_(True),
            DbUserVideoGame.rank.isnot(None),
            DbUserVideoGame.rank > old_rank,
        ).update(
            {DbUserVideoGame.rank: DbUserVideoGame.rank - 1},
            synchronize_session=False,
        )

    target = max(1, min(request.position, _placed_count(db, user_pk) + 1))
    db.query(DbUserVideoGame).filter(
        DbUserVideoGame.user_id == user_pk,
        DbUserVideoGame.on_rankings.is_(True),
        DbUserVideoGame.rank.isnot(None),
        DbUserVideoGame.rank >= target,
    ).update(
        {DbUserVideoGame.rank: DbUserVideoGame.rank + 1}, synchronize_session=False
    )

    tracker.rank = target
    tracker.ranked_at = utc_now()
    db.commit()
    db.refresh(tracker)
    return tracker


@router.post(
    '/users/me/games/{game_id}',
    response_model=UserVideoGameResponse,
    status_code=status.HTTP_201_CREATED,
)
def mark_game(
    game_id: str,
    request: UserVideoGameCreate,
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
):
    """Add a game to the user's lists (idempotent — merges list membership)."""
    user_pk = current_user[0].pk
    game = _get_game(db, game_id)
    tracker = _get_tracker(db, user_pk, game.pk)
    data = request.model_dump(exclude_unset=True)

    if tracker is None:
        was_on_rankings = False
        old_rank = None
        tracker = DbUserVideoGame(
            user_id=user_pk,
            game_id=game.pk,
            on_watchlist=bool(data.get('on_watchlist', False)),
            on_rankings=bool(data.get('on_rankings', False)),
            notes=data.get('notes'),
            completed_at=data.get('completed_at'),
            is_100_percent=bool(data.get('is_100_percent', False)),
        )
        db.add(tracker)
    else:
        was_on_rankings = tracker.on_rankings
        old_rank = tracker.rank
        for key in (
            'on_watchlist',
            'on_rankings',
            'notes',
            'completed_at',
            'is_100_percent',
        ):
            if key in data:
                setattr(tracker, key, data[key])

    # A game only holds a rank while it's on the ranked list AND was already
    # placed. Entering Rankings (or leaving it) resets to unplaced so it lands
    # in the "to rank" bucket rather than at a stale/leftover position —
    # unless it's the first ranked game, which auto-places at #1 (#289).
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


@router.put('/users/me/games/{game_id}', response_model=UserVideoGameResponse)
def update_user_game(
    game_id: str,
    request: UserVideoGameUpdate,
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
):
    """Update list membership, rank, notes, or 100% flag for a tracked game."""
    user_pk = current_user[0].pk
    game = _get_game(db, game_id)
    tracker = _get_tracker(db, user_pk, game.pk)
    if not tracker:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail='Game not marked'
        )

    old_rank = tracker.rank
    was_on_rankings = tracker.on_rankings
    data = request.model_dump(exclude_unset=True)
    for key, value in data.items():
        setattr(tracker, key, value)
    enforce_single_home(tracker, data)
    default_completed_at(tracker, was_on_rankings)

    # Entering Rankings (or leaving it) resets to unplaced so a stale/leftover
    # rank never places the game automatically; it lands in "to rank" instead.
    if not tracker.on_rankings or not was_on_rankings:
        tracker.rank = None
        tracker.ranked_at = None

    # A removed placement leaves a gap — shift everything below it up.
    if old_rank is not None and tracker.rank is None:
        _close_rank_gap(db, user_pk, old_rank)

    # If it's on neither list, drop the tracker entirely.
    if not tracker.on_watchlist and not tracker.on_rankings:
        response = UserVideoGameResponse.model_validate(tracker)
        db.delete(tracker)
        db.commit()
        return response

    db.commit()
    db.refresh(tracker)
    return tracker


@router.delete('/users/me/games/{game_id}', status_code=status.HTTP_204_NO_CONTENT)
def unmark_game(
    game_id: str,
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
):
    user_pk = current_user[0].pk
    game = _get_game(db, game_id)
    tracker = _get_tracker(db, user_pk, game.pk)
    if not tracker:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail='Game not marked'
        )
    _close_rank_gap(db, user_pk, tracker.rank)
    db.delete(tracker)
    db.commit()
    return None
