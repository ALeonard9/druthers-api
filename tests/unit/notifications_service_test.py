# pylint: disable=missing-module-docstring, missing-function-docstring
"""
Unit tests for app.services.notifications.

Verifies notification generation for movie releases, incoming friend requests,
accepted friend requests, and sweep_all.
"""

import uuid
from datetime import datetime, timedelta, timezone
from app.db.models import DbFriendship
from app.db.models_sandbox import DbMovie, DbUserMovie
from app.services.friendships import FriendshipStatus
from app.services.notifications import (
    _existing_keys,
    sweep_all,
    sweep_friend_requests,
    sweep_movie_releases,
)


def test_existing_keys_empty(test_db_session):
    res = _existing_keys(test_db_session, 1, [])
    assert res == set()


def test_sweep_movie_releases(test_db_session, test_client, test_create_user):
    user = test_create_user(test_client)[0]

    release_date = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0, tzinfo=None
    ) + timedelta(days=3)
    movie = DbMovie(title="Dune 2", release_date=release_date, tmdb=100)
    test_db_session.add(movie)
    test_db_session.commit()

    tracker = DbUserMovie(user_id=user.pk, movie_id=movie.pk, on_watchlist=True)
    test_db_session.add(tracker)
    test_db_session.commit()

    created = sweep_movie_releases(test_db_session, user.pk)
    assert created == 1
    test_db_session.commit()

    created_again = sweep_movie_releases(test_db_session, user.pk)
    assert created_again == 0


def test_sweep_friend_requests(test_db_session, test_client, test_create_user):
    users = test_create_user(test_client, user_count=2)
    u1, u2 = users[0], users[1]

    low_id = min(u1.pk, u2.pk)
    high_id = max(u1.pk, u2.pk)
    friendship = DbFriendship(
        id=str(uuid.uuid4()),
        user_low_id=low_id,
        user_high_id=high_id,
        requested_by_id=u2.pk,
        status=FriendshipStatus.PENDING,
        requested_at=datetime.now(),
    )
    test_db_session.add(friendship)
    test_db_session.commit()

    created = sweep_friend_requests(test_db_session, u1.pk)
    assert created >= 1


def test_sweep_all(test_db_session, test_client, test_create_user):
    user = test_create_user(test_client)[0]
    res = sweep_all(test_db_session, user.pk)
    assert res == 0
