"""
Notification generators.

Each generator sweeps one event source and upserts ``DbNotification`` rows
keyed on ``dedupe_key``, so sweeps are idempotent and safe to run on every
fetch. Today they run lazily when a client reads its notifications; a future
push channel (mobile, Telegram) can call the same functions from a cron and
then deliver whatever rows come back unread.
"""

from datetime import datetime, timedelta, timezone
from typing import List

from sqlalchemy.orm import Session

from app.db.db_friendship import list_with_other_party
from app.db.models_sandbox import DbMovie, DbNotification, DbUserMovie
from app.services.friendships import FriendshipStatus

RELEASE_WINDOW_DAYS = 7


def _existing_keys(db: Session, user_pk: int, keys: List[str]) -> set:
    if not keys:
        return set()
    rows = db.query(DbNotification.dedupe_key).filter(
        DbNotification.user_id == user_pk,
        DbNotification.dedupe_key.in_(keys),
    )
    return {row.dedupe_key for row in rows}


def sweep_movie_releases(db: Session, user_pk: int) -> int:
    """
    Notify about watchlist movies whose release date lands within the next
    ``RELEASE_WINDOW_DAYS`` days. Returns the number of notifications created.
    """
    # release_date is stored tz-naive (parsed from TMDB), so compare naive.
    today = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0, tzinfo=None
    )
    window_end = today + timedelta(days=RELEASE_WINDOW_DAYS)

    trackers = (
        db.query(DbUserMovie)
        .join(DbMovie, DbUserMovie.movie_id == DbMovie.pk)
        .filter(
            DbUserMovie.user_id == user_pk,
            DbUserMovie.on_watchlist.is_(True),
            DbMovie.release_date.isnot(None),
            DbMovie.release_date >= today,
            DbMovie.release_date <= window_end,
        )
        .all()
    )
    if not trackers:
        return 0

    keys = [f'movie_release:{t.movie.id}' for t in trackers]
    existing = _existing_keys(db, user_pk, keys)
    created = 0
    for tracker in trackers:
        movie = tracker.movie
        key = f'movie_release:{movie.id}'
        if key in existing:
            continue
        # %-d is glibc-only (the container is musl/alpine), so build manually.
        release_day = f'{movie.release_date.strftime("%B")} {movie.release_date.day}'
        db.add(
            DbNotification(
                user_id=user_pk,
                type='movie_release',
                title=f'{movie.title} hits theaters soon',
                body=f'{movie.title} releases {release_day} - it\'s on your watchlist.',
                category='movie',
                entity_id=movie.id,
                dedupe_key=key,
            )
        )
        created += 1
    return created


def _raise_friend_notifications(db: Session, user_pk: int, spec: dict, rows) -> int:
    """
    Upsert one notification per (friendship, other-user) row, by dedupe_key.

    ``spec`` carries ``type``, ``title``, and ``body_for(other_user)`` - a
    dict rather than three parameters so the two call sites in
    :func:`sweep_friend_requests` read as one shape apiece.
    """
    if not rows:
        return 0
    notif_type = spec['type']
    keys = [f'{notif_type}:{friendship.id}' for friendship, _ in rows]
    existing = _existing_keys(db, user_pk, keys)
    created = 0
    for friendship, other in rows:
        key = f'{notif_type}:{friendship.id}'
        if key in existing:
            continue
        db.add(
            DbNotification(
                user_id=user_pk,
                type=notif_type,
                title=spec['title'],
                body=spec['body_for'](other),
                category='friend_request',
                entity_id=friendship.id,
                dedupe_key=key,
            )
        )
        created += 1
    return created


def sweep_friend_requests(db: Session, user_pk: int) -> int:
    """
    Friend-request notifications (#282): an incoming request notifies the
    recipient; acceptance notifies the original sender. Declining or
    cancelling notifies nobody.

    #275 deletes the friendship row outright on decline or cancel rather than
    recording a terminal status - there is nothing to sweep a "declined"
    event from. That also means a pending-request notification can outlive
    the request it points at (declined/cancelled, or simply accepted by the
    recipient themselves). Rather than let it deep-link into nothing, this
    sweep deletes any pending notification whose friendship is no longer in
    the live incoming set *before* raising anything new. Returns the number
    of notifications created or removed, so the caller knows to commit.
    """
    incoming = list_with_other_party(
        db, user_pk, FriendshipStatus.PENDING, requested_by_me=False
    )
    live_keys = {f'friend_request:{friendship.id}' for friendship, _ in incoming}

    stale_filters = [
        DbNotification.user_id == user_pk,
        DbNotification.type == 'friend_request',
    ]
    if live_keys:
        stale_filters.append(DbNotification.dedupe_key.notin_(live_keys))
    removed = 0
    for notification in db.query(DbNotification).filter(*stale_filters):
        db.delete(notification)
        removed += 1

    def _name(user):
        return user.display_name or user.handle or 'Someone'

    created = _raise_friend_notifications(
        db,
        user_pk,
        {
            'type': 'friend_request',
            'title': 'New friend request',
            'body_for': lambda sender: f'{_name(sender)} wants to be friends.',
        },
        incoming,
    )

    accepted = list_with_other_party(
        db, user_pk, FriendshipStatus.ACCEPTED, requested_by_me=True
    )
    created += _raise_friend_notifications(
        db,
        user_pk,
        {
            'type': 'friend_request_accepted',
            'title': 'Friend request accepted',
            'body_for': lambda other: f'{_name(other)} accepted your friend request.',
        },
        accepted,
    )

    return removed + created


def sweep_all(db: Session, user_pk: int) -> int:
    """Run every generator for one user. Commits if anything changed."""
    changed = sweep_movie_releases(db, user_pk) + sweep_friend_requests(db, user_pk)
    if changed:
        db.commit()
    return changed
