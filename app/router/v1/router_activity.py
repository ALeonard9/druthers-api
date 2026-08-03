# pylint: disable=missing-function-docstring
"""
Cross-domain Activity Log and "I'm bored" recommendation.

Neither concept is owned by a single tracker domain, so unlike Schedule (which
lives in router_tv.py because it's purely TV data), this is its own router
that reads across Movies/TV/Games/Books.
"""

import random
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import case, func
from sqlalchemy.orm import Session, joinedload

from app.db.database import get_db
from app.db.models_sandbox import (
    DbTVEpisode,
    DbUserBook,
    DbUserMovie,
    DbUserTVEpisode,
    DbUserTVShow,
    DbUserVideoGame,
)
from app.auth.oauth2 import get_current_user
from app.schemas.schemas_sandbox import ActivityItem, BoredItem, BoredResponse

router = APIRouter(prefix='/v1', tags=['Activity'])

# Hard ceiling on the returned feed; also bounds per-domain SQL fetches.
MAX_FEED = 200


def _tracker_occurred_at(tracker, action):
    """
    The semantic timestamp for a tracker's feed entry. updated_at is a
    technical "row was touched" column — it is only ever a last-resort
    fallback here, never the meaning (#141 follow-up, 2026-07-19).
    """
    if action == 'ranked':
        return tracker.ranked_at or tracker.updated_at
    if action == 'marked_done':
        return getattr(tracker, 'completed_at', None) or tracker.updated_at
    # watchlist_added: when the row was created is when it was added.
    return tracker.created_at or tracker.updated_at


def _bounded_tracker_rows(db: Session, model, user_pk: int, *loader_options):
    """
    Rows on watchlist or rankings, newest-by-_tracker_occurred_at first,
    bounded to MAX_FEED in SQL — mirrors _tracker_occurred_at's branching so
    the SQL order matches the Python-computed occurred_at exactly. Movies/TV
    shows/games/books share these column names (on_rankings, rank, ranked_at,
    completed_at, created_at, updated_at).
    """
    occurred_at = case(
        (
            (model.on_rankings.is_(True)) & (model.rank.isnot(None)),
            func.coalesce(model.ranked_at, model.updated_at),
        ),
        (
            model.on_rankings.is_(True),
            func.coalesce(model.completed_at, model.updated_at),
        ),
        else_=func.coalesce(model.created_at, model.updated_at),
    )
    return (
        db.query(model)
        .options(*loader_options)
        .filter(
            model.user_id == user_pk,
            (model.on_rankings.is_(True)) | (model.on_watchlist.is_(True)),
        )
        .order_by(occurred_at.desc())
        .limit(MAX_FEED)
        .all()
    )


# --- Activity Log ---
def _movie_activity(db: Session, user_pk: int) -> List[ActivityItem]:
    trackers = _bounded_tracker_rows(
        db, DbUserMovie, user_pk, joinedload(DbUserMovie.movie)
    )
    items = []
    for t in trackers:
        if t.on_rankings and t.rank is not None:
            action = 'ranked'
        elif t.on_rankings:
            action = 'marked_done'
        elif t.on_watchlist:
            action = 'watchlist_added'
        else:
            continue
        items.append(
            ActivityItem(
                category='movie',
                action=action,
                title=t.movie.title,
                entity_id=t.movie.id,
                poster_url=t.movie.poster_url,
                rank=t.rank if action == 'ranked' else None,
                occurred_at=_tracker_occurred_at(t, action),
            )
        )
    return items


def _tv_show_activity(db: Session, user_pk: int) -> List[ActivityItem]:
    trackers = _bounded_tracker_rows(
        db, DbUserTVShow, user_pk, joinedload(DbUserTVShow.tv_show)
    )
    items = []
    for t in trackers:
        if t.on_rankings and t.rank is not None:
            action = 'ranked'
        elif t.on_rankings:
            action = 'marked_done'
        elif t.on_watchlist:
            action = 'watchlist_added'
        else:
            continue
        items.append(
            ActivityItem(
                category='tv_show',
                action=action,
                title=t.tv_show.title,
                entity_id=t.tv_show.id,
                poster_url=t.tv_show.poster_url,
                rank=t.rank if action == 'ranked' else None,
                occurred_at=_tracker_occurred_at(t, action),
            )
        )
    return items


def _episode_activity(db: Session, user_pk: int) -> List[ActivityItem]:
    rows = (
        db.query(DbUserTVEpisode)
        .options(joinedload(DbUserTVEpisode.episode).joinedload(DbTVEpisode.tv_show))
        .filter(DbUserTVEpisode.user_id == user_pk, DbUserTVEpisode.watched == 1)
        # Episode marks dwarf every other domain (tens of thousands once a
        # library is imported) and occurred_at is updated_at here, so only the
        # newest MAX_FEED can survive the merged sort — bound it in SQL.
        .order_by(
            func.coalesce(DbUserTVEpisode.watched_at, DbUserTVEpisode.updated_at).desc()
        )
        .limit(MAX_FEED)
        .all()
    )
    items = []
    for row in rows:
        ep = row.episode
        show = ep.tv_show
        label = None
        if ep.season is not None and ep.season_number is not None:
            label = f'S{ep.season}E{ep.season_number}'
        items.append(
            ActivityItem(
                category='tv_episode',
                action='watched_episode',
                title=show.title,
                subtitle=f'{label} - {ep.title}' if label else ep.title,
                entity_id=show.id,
                poster_url=show.poster_url,
                occurred_at=row.watched_at or row.updated_at,
            )
        )
    return items


def _game_activity(db: Session, user_pk: int) -> List[ActivityItem]:
    trackers = _bounded_tracker_rows(
        db, DbUserVideoGame, user_pk, joinedload(DbUserVideoGame.game)
    )
    items = []
    for t in trackers:
        if t.on_rankings and t.rank is not None:
            action = 'ranked'
        elif t.on_rankings:
            action = 'marked_done'
        elif t.on_watchlist:
            action = 'watchlist_added'
        else:
            continue
        items.append(
            ActivityItem(
                category='game',
                action=action,
                title=t.game.title,
                subtitle='100%' if t.is_100_percent else None,
                entity_id=t.game.id,
                poster_url=t.game.poster_url,
                rank=t.rank if action == 'ranked' else None,
                occurred_at=_tracker_occurred_at(t, action),
            )
        )
    return items


def _book_activity(db: Session, user_pk: int) -> List[ActivityItem]:
    trackers = _bounded_tracker_rows(
        db, DbUserBook, user_pk, joinedload(DbUserBook.book)
    )
    items = []
    for t in trackers:
        if t.on_rankings and t.rank is not None:
            action = 'ranked'
        elif t.on_rankings:
            action = 'marked_done'
        elif t.on_watchlist:
            action = 'watchlist_added'
        else:
            continue
        items.append(
            ActivityItem(
                category='book',
                action=action,
                title=t.book.title,
                entity_id=t.book.id,
                poster_url=t.book.poster_url,
                rank=t.rank if action == 'ranked' else None,
                occurred_at=_tracker_occurred_at(t, action),
            )
        )
    return items


@router.get('/users/me/activity', response_model=List[ActivityItem])
def get_activity(
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
    category: Optional[str] = None,
    limit: int = 50,
):
    """Cross-domain "what have I been up to" feed, newest first."""
    user_pk = current_user[0].pk
    items = (
        _movie_activity(db, user_pk)
        + _tv_show_activity(db, user_pk)
        + _episode_activity(db, user_pk)
        + _game_activity(db, user_pk)
        + _book_activity(db, user_pk)
    )
    if category:
        items = [i for i in items if i.category == category]
    items.sort(key=lambda i: i.occurred_at, reverse=True)
    return items[: max(1, min(limit, MAX_FEED))]


# --- "I'm bored" recommendation ---
def _movie_pool(db: Session, user_pk: int) -> List[BoredItem]:
    trackers = (
        db.query(DbUserMovie)
        .options(joinedload(DbUserMovie.movie))
        .filter(DbUserMovie.user_id == user_pk, DbUserMovie.on_watchlist.is_(True))
        .all()
    )
    return [
        BoredItem(
            category='movie',
            title=t.movie.title,
            entity_id=t.movie.id,
            poster_url=t.movie.poster_url,
        )
        for t in trackers
    ]


def _tv_show_pool(db: Session, user_pk: int) -> List[BoredItem]:
    trackers = (
        db.query(DbUserTVShow)
        .options(joinedload(DbUserTVShow.tv_show))
        .filter(DbUserTVShow.user_id == user_pk, DbUserTVShow.on_watchlist.is_(True))
        .all()
    )
    return [
        BoredItem(
            category='tv_show',
            title=t.tv_show.title,
            entity_id=t.tv_show.id,
            poster_url=t.tv_show.poster_url,
        )
        for t in trackers
    ]


def _game_pool(db: Session, user_pk: int) -> List[BoredItem]:
    trackers = (
        db.query(DbUserVideoGame)
        .options(joinedload(DbUserVideoGame.game))
        .filter(
            DbUserVideoGame.user_id == user_pk, DbUserVideoGame.on_watchlist.is_(True)
        )
        .all()
    )
    return [
        BoredItem(
            category='game',
            title=t.game.title,
            entity_id=t.game.id,
            poster_url=t.game.poster_url,
        )
        for t in trackers
    ]


def _book_pool(db: Session, user_pk: int) -> List[BoredItem]:
    trackers = (
        db.query(DbUserBook)
        .options(joinedload(DbUserBook.book))
        .filter(DbUserBook.user_id == user_pk, DbUserBook.on_watchlist.is_(True))
        .all()
    )
    return [
        BoredItem(
            category='book',
            title=t.book.title,
            subtitle=t.book.authors,
            entity_id=t.book.id,
            poster_url=t.book.poster_url,
        )
        for t in trackers
    ]


@router.get('/users/me/bored', response_model=BoredResponse)
def get_bored_pick(
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
    exclude: Optional[str] = None,
):
    """
    Randomly pick one item off the user's watchlists/bucket lists, across
    every domain. ``exclude`` (comma-separated entity ids) lets the client
    re-roll without repeating the item(s) it's already shown.
    """
    user_pk = current_user[0].pk
    pool = (
        _movie_pool(db, user_pk)
        + _tv_show_pool(db, user_pk)
        + _game_pool(db, user_pk)
        + _book_pool(db, user_pk)
    )
    if not pool:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Nothing on your to-be-consumed lists yet',
        )
    excluded_ids = set(exclude.split(',')) if exclude else set()
    candidates = [item for item in pool if item.entity_id not in excluded_ids] or pool
    pick = random.choice(candidates)
    return BoredResponse(pick=pick, pool_size=len(pool))
