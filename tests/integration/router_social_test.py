# pylint: disable=missing-module-docstring, missing-function-docstring, import-outside-toplevel

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.db.models import DbFollow, DbFriendship, VisibilityTier
from app.db.models_sandbox import DbMovie, DbUserMovie
from app.services.friendships import FriendshipStatus


def test_social_context_empty(test_client: TestClient):
    db = test_client.test_db_session
    user = test_client.first_user
    movie = DbMovie(id='m1', title='Movie 1', year=2020)
    db.add(movie)
    db.commit()

    response = test_client.get(
        f'/v1/movies/{movie.id}/social',
        headers={'Authorization': f'Bearer {user.token}'},
    )
    assert response.status_code == 200
    assert response.json() == []


def test_social_context_populated(test_client: TestClient, test_create_user):
    db = test_client.test_db_session
    user = test_client.first_user
    movie = DbMovie(id='m2', title='Movie 2', year=2020)
    db.add(movie)
    db.commit()

    extra_users = test_create_user(test_client, 3)
    friend = extra_users[0]
    followee = extra_users[1]
    unrelated = extra_users[2]

    friend.handle = 'friend_handle'
    friend.visibility_profile = VisibilityTier.PUBLIC
    friend.visibility_movies = VisibilityTier.PUBLIC
    friend.visibility_notes_movies = VisibilityTier.FRIENDS
    friend.visibility_watchlist_movies = VisibilityTier.PUBLIC

    followee.handle = 'followee_handle'
    followee.visibility_profile = VisibilityTier.PUBLIC
    followee.visibility_movies = VisibilityTier.PUBLIC
    followee.visibility_notes_movies = VisibilityTier.PRIVATE
    followee.visibility_watchlist_movies = VisibilityTier.PUBLIC

    unrelated.handle = 'unrelated_handle'
    unrelated.visibility_profile = VisibilityTier.PUBLIC
    unrelated.visibility_movies = VisibilityTier.PUBLIC

    db.add_all([friend, followee, unrelated])
    db.commit()

    low, high = sorted([user.pk, friend.pk])
    db.add(
        DbFriendship(
            user_low_id=low,
            user_high_id=high,
            requested_by_id=user.pk,
            status=FriendshipStatus.ACCEPTED,
            responded_at=datetime.now(timezone.utc),
        )
    )
    db.add(DbFollow(follower_id=user.pk, followee_id=followee.pk))

    db.add(
        DbUserMovie(
            user_id=friend.pk,
            movie_id=movie.pk,
            on_rankings=True,
            rank=1,
            notes='Great',
        )
    )
    db.add(
        DbUserMovie(
            user_id=followee.pk,
            movie_id=movie.pk,
            on_watchlist=True,
            notes='Followee Notes',
        )
    )
    db.add(
        DbUserMovie(user_id=unrelated.pk, movie_id=movie.pk, on_rankings=True, rank=2)
    )
    db.commit()

    response = test_client.get(
        f'/v1/movies/{movie.id}/social',
        headers={'Authorization': f'Bearer {user.token}'},
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2

    handles = {d['handle'] for d in data}
    assert friend.handle in handles
    assert followee.handle in handles
    assert unrelated.handle not in handles

    for d in data:
        if d['handle'] == friend.handle:
            assert d['relationship'] == 'friends'
            assert d['rank'] == 1
            assert d['notes'] == 'Great'
            assert d['on_watchlist'] is False
        elif d['handle'] == followee.handle:
            assert d['relationship'] == 'follows'
            assert d['rank'] is None
            assert d['notes'] is None
            assert d['on_watchlist'] is True


def test_social_context_four_domains(test_client: TestClient):
    db = test_client.test_db_session
    user = test_client.first_user
    from app.db.models_sandbox import DbBook, DbTVShow, DbVideoGame

    tv = DbTVShow(id='t1', title='TV')
    book = DbBook(id='b1', title='Book')
    game = DbVideoGame(id='g1', title='Game')
    db.add_all([tv, book, game])
    db.commit()

    for endpoint, item_id in [
        ('/v1/tv', tv.id),
        ('/v1/books', book.id),
        ('/v1/games', game.id),
    ]:
        resp = test_client.get(
            f'{endpoint}/{item_id}/social',
            headers={'Authorization': f'Bearer {user.token}'},
        )
        assert resp.status_code == 200
        assert resp.json() == []
