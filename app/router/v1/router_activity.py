# pylint: disable=missing-function-docstring
"""
Cross-domain Activity Log and "I'm bored" recommendation.

Neither concept is owned by a single tracker domain, so unlike Schedule (which
lives in router_tv.py because it's purely TV data), this is its own router
that reads across Movies/TV/Games/Books.
"""

import base64
import binascii
import json
import random
from datetime import datetime
from typing import List, NamedTuple, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, case, func, literal, or_, select, union_all
from sqlalchemy.orm import Session, joinedload

from app.auth.oauth2 import get_current_user
from app.db.database import get_db
from app.db.models import DbFollow, DbFriendship, DbUser
from app.db.models_sandbox import (
    DbBook,
    DbMovie,
    DbTVShow,
    DbTVEpisode,
    DbUserBook,
    DbUserMovie,
    DbUserTVEpisode,
    DbUserTVShow,
    DbUserVideoGame,
    DbVideoGame,
)
from app.schemas.schemas_sandbox import (
    ActivityActor,
    ActivityItem,
    BoredItem,
    BoredResponse,
    SocialActivityItem,
    SocialActivityPage,
)
from app.services.friendships import FriendshipStatus
from app.services.shelves import SHELVES, Shelf
from app.services.visibility import VisibilityTier

router = APIRouter(prefix='/v1', tags=['Activity'])

# Hard ceiling on the returned feed; also bounds per-domain SQL fetches.
MAX_FEED = 200

# A social page runs one query per shelf and merges those bounded result sets.
# Keeping this below MAX_FEED limits both the response and every SQL fetch.
MAX_SOCIAL_FEED = 100

_ACTIVITY_CATEGORIES = {
    'movies': 'movie',
    'tv': 'tv_show',
    'books': 'book',
    'games': 'game',
}


class _SocialCursor(NamedTuple):
    """Stable global sort key for one social-feed row."""

    occurred_at: datetime
    shelf_order: int
    tracker_pk: int


class _SocialRow(NamedTuple):
    """Response item plus the private fields keyset paging needs."""

    cursor: _SocialCursor
    item: SocialActivityItem


def _encode_cursor(cursor: _SocialCursor) -> str:
    payload = json.dumps(
        [cursor.occurred_at.isoformat(), cursor.shelf_order, cursor.tracker_pk],
        separators=(',', ':'),
    ).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip('=')


def _decode_cursor(raw: str) -> _SocialCursor:
    try:
        padding = '=' * (-len(raw) % 4)
        occurred_at, shelf_order, tracker_pk = json.loads(
            base64.urlsafe_b64decode(raw + padding)
        )
        cursor = _SocialCursor(
            datetime.fromisoformat(occurred_at), int(shelf_order), int(tracker_pk)
        )
        if not 0 <= cursor.shelf_order < len(SHELVES) or cursor.tracker_pk < 1:
            raise ValueError
        return cursor
    except (
        TypeError,
        ValueError,
        UnicodeDecodeError,
        binascii.Error,
        json.JSONDecodeError,
    ) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail='Invalid activity feed cursor',
        ) from exc


def _feed_relationships(viewer_pk: int):
    """
    Owners this viewer may receive activity from, deduplicated in SQL.

    Each branch starts from an indexed viewer-side relationship column. The
    grouped result is at most the viewer's own graph size, and an accepted
    friendship wins over a duplicate follow so friends-tier shelves remain
    visible without widening follow-only access.
    """
    friend_from_low = select(
        DbFriendship.user_high_id.label('owner_id'),
        literal(1).label('is_friend'),
    ).where(
        DbFriendship.user_low_id == viewer_pk,
        DbFriendship.status == FriendshipStatus.ACCEPTED,
    )
    friend_from_high = select(
        DbFriendship.user_low_id.label('owner_id'),
        literal(1).label('is_friend'),
    ).where(
        DbFriendship.user_high_id == viewer_pk,
        DbFriendship.status == FriendshipStatus.ACCEPTED,
    )
    followed = select(
        DbFollow.followee_id.label('owner_id'), literal(0).label('is_friend')
    ).where(DbFollow.follower_id == viewer_pk)
    edges = union_all(friend_from_low, friend_from_high, followed).subquery()
    return (
        select(
            edges.c.owner_id,
            func.max(edges.c.is_friend).label('is_friend'),
        )
        .group_by(edges.c.owner_id)
        .subquery()
    )


def _social_shelf_rows(  # pylint: disable=too-many-arguments, too-many-positional-arguments, too-many-locals
    db: Session,
    relationships,
    shelf: Shelf,
    shelf_order: int,
    cursor: Optional[_SocialCursor],
    limit: int,
) -> List[_SocialRow]:
    """One shelf's currently authorized candidates, bounded in SQL."""
    tracker_model = shelf.tracker_model
    catalog_model = shelf.catalog_model
    ranked = and_(tracker_model.on_rankings.is_(True), tracker_model.rank.isnot(None))
    watchlisted = and_(tracker_model.on_watchlist.is_(True), ~ranked)
    occurred_at = case(
        (
            ranked,
            func.coalesce(tracker_model.ranked_at, tracker_model.updated_at),
        ),
        else_=func.coalesce(tracker_model.created_at, tracker_model.updated_at),
    )

    friend = relationships.c.is_friend == 1

    # The SQL mirror of ``visibility.resolve_tier``: since api#298 a shelf tier
    # is nullable, and null means "inherit ``default_privacy``" rather than
    # "private". Comparing the raw column would make every inherited shelf
    # vanish from the feed, because ``NULL = 'friends'`` is null, not false.
    def _resolved(field: str):
        return func.coalesce(getattr(DbUser, field), DbUser.default_privacy)

    def _admits(tier):
        return or_(
            tier == VisibilityTier.PUBLIC,
            and_(friend, tier == VisibilityTier.FRIENDS),
        )

    # The profile tier stays explicit and non-null, so it is compared directly.
    profile_visible = _admits(DbUser.visibility_profile)
    ranked_visible = and_(
        profile_visible,
        _admits(_resolved(shelf.visibility_tier)),
    )
    watchlist_visible = and_(
        ranked_visible,
        _admits(_resolved(shelf.watchlist_visibility_tier)),
    )
    event_visible = or_(
        and_(ranked, ranked_visible),
        and_(watchlisted, watchlist_visible),
    )

    query = (
        db.query(tracker_model, catalog_model, DbUser, occurred_at.label('occurred_at'))
        .join(relationships, relationships.c.owner_id == tracker_model.user_id)
        .join(DbUser, DbUser.pk == tracker_model.user_id)
        .join(
            catalog_model,
            getattr(tracker_model, shelf.join_col) == catalog_model.pk,
        )
        .filter(
            DbUser.share_activity.is_(True),
            # A disabled account is invisible everywhere a deleted one
            # would be (#344 D2) - no activity-feed contributions.
            DbUser.disabled_at.is_(None),
            event_visible,
        )
    )
    if cursor is not None:
        query = query.filter(
            or_(
                occurred_at < cursor.occurred_at,
                and_(
                    occurred_at == cursor.occurred_at,
                    literal(shelf_order) < cursor.shelf_order,
                ),
                and_(
                    occurred_at == cursor.occurred_at,
                    literal(shelf_order) == cursor.shelf_order,
                    tracker_model.pk < cursor.tracker_pk,
                ),
            )
        )

    rows = (
        query.order_by(occurred_at.desc(), tracker_model.pk.desc()).limit(limit).all()
    )
    category = _ACTIVITY_CATEGORIES[shelf.category]
    result = []
    for tracker, catalog, actor, timestamp in rows:
        action = (
            'ranked'
            if tracker.on_rankings and tracker.rank is not None
            else 'watchlist_added'
        )
        result.append(
            _SocialRow(
                cursor=_SocialCursor(timestamp, shelf_order, tracker.pk),
                item=SocialActivityItem(
                    category=category,
                    action=action,
                    title=catalog.title,
                    subtitle=(
                        '100%'
                        if category == 'game' and tracker.is_100_percent
                        else None
                    ),
                    entity_id=catalog.id,
                    poster_url=catalog.poster_url,
                    rank=tracker.rank if action == 'ranked' else None,
                    occurred_at=timestamp,
                    actor=ActivityActor(
                        id=actor.id,
                        handle=actor.handle,
                        display_name=actor.display_name,
                    ),
                ),
            )
        )
    return result


def _tracker_occurred_at(tracker, action):
    """
    The semantic timestamp for a tracker's feed entry. updated_at is a
    technical "row was touched" column - it is only ever a last-resort
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
    bounded to MAX_FEED in SQL - mirrors _tracker_occurred_at's branching so
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
        # newest MAX_FEED can survive the merged sort - bound it in SQL.
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


@router.get('/users/me/feed', response_model=SocialActivityPage)
def get_social_activity(
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
    limit: int = Query(default=50, ge=1, le=MAX_SOCIAL_FEED),
    cursor: Optional[str] = Query(default=None, max_length=256),
):
    """
    Recent ranking and watchlist activity from friends and followed users.

    Relationships and the owner's current sharing tiers are joined into each
    shelf query. Nothing about visibility is copied onto a tracker row, so an
    unfriend, unfollow, opt-out, or tier reduction takes effect on old entries
    immediately. Keyset paging avoids an increasingly expensive offset scan
    as a caller moves deeper into the feed.
    """
    decoded_cursor = _decode_cursor(cursor) if cursor else None
    relationships = _feed_relationships(current_user[0].pk)
    fetch_limit = limit + 1
    rows = [
        row
        for shelf_order, shelf in enumerate(SHELVES)
        for row in _social_shelf_rows(
            db,
            relationships,
            shelf,
            shelf_order,
            decoded_cursor,
            fetch_limit,
        )
    ]
    rows.sort(key=lambda row: row.cursor, reverse=True)
    page = rows[:limit]
    return SocialActivityPage(
        items=[row.item for row in page],
        next_cursor=(
            _encode_cursor(page[-1].cursor) if len(rows) > limit and page else None
        ),
    )


# --- "I'm bored" recommendation ---
@router.get('/users/me/bored', response_model=BoredResponse)
def get_bored_pick(
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
    exclude: Optional[str] = None,
):
    # pylint: disable=too-many-locals
    """
    Randomly pick one item off the user's watchlists/bucket lists, across
    every domain. ``exclude`` (comma-separated entity ids) lets the client
    re-roll without repeating the item(s) it's already shown.
    """
    user_pk = current_user[0].pk
    excluded_ids = set(exclude.split(',')) if exclude else set()

    sources = [
        (DbUserMovie, DbMovie, DbUserMovie.movie, 'movie'),
        (DbUserTVShow, DbTVShow, DbUserTVShow.tv_show, 'tv_show'),
        (DbUserVideoGame, DbVideoGame, DbUserVideoGame.game, 'game'),
        (DbUserBook, DbBook, DbUserBook.book, 'book'),
    ]

    def _get_counts(exclude_set):
        counts = []
        for model, entity_model, _, _ in sources:
            q = (
                db.query(model)
                .join(entity_model)
                .filter(model.user_id == user_pk, model.on_watchlist.is_(True))
            )
            if exclude_set:
                q = q.filter(entity_model.id.notin_(exclude_set))
            counts.append(q.count())
        return counts

    counts = _get_counts(excluded_ids)
    total_pool = sum(counts)

    if total_pool == 0 and excluded_ids:
        counts = _get_counts(set())
        total_pool = sum(counts)
        excluded_ids = set()

    if total_pool == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Nothing on your to-be-consumed lists yet',
        )

    pick_idx = random.randint(0, total_pool - 1)

    pick = None
    running_total = 0
    for count, (model, entity_model, entity_rel, category) in zip(counts, sources):
        if running_total <= pick_idx < running_total + count:
            offset = pick_idx - running_total
            q = (
                db.query(model)
                .join(entity_model)
                .options(joinedload(entity_rel))
                .filter(model.user_id == user_pk, model.on_watchlist.is_(True))
            )
            if excluded_ids:
                q = q.filter(entity_model.id.notin_(excluded_ids))

            t = q.order_by(model.pk).offset(offset).first()
            if not t:
                continue

            if category == 'movie':
                pick = BoredItem(
                    category=category,
                    title=t.movie.title,
                    entity_id=t.movie.id,
                    poster_url=t.movie.poster_url,
                )
            elif category == 'tv_show':
                pick = BoredItem(
                    category=category,
                    title=t.tv_show.title,
                    entity_id=t.tv_show.id,
                    poster_url=t.tv_show.poster_url,
                )
            elif category == 'game':
                pick = BoredItem(
                    category=category,
                    title=t.game.title,
                    entity_id=t.game.id,
                    poster_url=t.game.poster_url,
                )
            elif category == 'book':
                pick = BoredItem(
                    category=category,
                    title=t.book.title,
                    subtitle=t.book.authors,
                    entity_id=t.book.id,
                    poster_url=t.book.poster_url,
                )
            break
        running_total += count

    if not pick:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Could not fetch a bored pick',
        )

    return BoredResponse(pick=pick, pool_size=total_pool)
