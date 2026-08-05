# pylint: disable=missing-module-docstring, missing-function-docstring, too-many-locals
"""
Unit tests for app.router.v1.router_activity.

Verifies get_activity endpoint and bored picker endpoint across movies, tv,
games, and books.
"""

from app.db.models_sandbox import (
    DbBook,
    DbMovie,
    DbTVShow,
    DbUserBook,
    DbUserMovie,
    DbUserTVShow,
    DbUserVideoGame,
    DbVideoGame,
)


def _get_auth_headers(client, auth_fixture, user):
    token = auth_fixture(client, user.email, user.plain_password)
    return {"Authorization": f"Bearer {token}"}


def test_get_activity_empty(test_client, test_create_user, test_authenticate_user):
    user = test_create_user(test_client)[0]
    headers = _get_auth_headers(test_client, test_authenticate_user, user)

    res = test_client.get("/v1/users/me/activity", headers=headers)
    assert res.status_code == 200
    assert res.json() == []


def test_get_activity_with_items(
    test_db_session, test_client, test_create_user, test_authenticate_user
):
    user = test_create_user(test_client)[0]
    user_pk = user.pk
    headers = _get_auth_headers(test_client, test_authenticate_user, user)

    movie = DbMovie(title="Dune", tmdb=10)
    test_db_session.add(movie)
    test_db_session.commit()

    tracker = DbUserMovie(user_id=user_pk, movie_id=movie.pk, on_watchlist=True)
    test_db_session.add(tracker)
    test_db_session.commit()

    res = test_client.get("/v1/users/me/activity", headers=headers)
    assert res.status_code == 200
    items = res.json()
    assert len(items) == 1
    assert items[0]["title"] == "Dune"
    assert items[0]["category"] == "movie"


def test_bored_empty(test_client, test_create_user, test_authenticate_user):
    user = test_create_user(test_client)[0]
    headers = _get_auth_headers(test_client, test_authenticate_user, user)

    res = test_client.get("/v1/users/me/bored", headers=headers)
    assert res.status_code == 404


def test_bored_with_watchlist_items(
    test_db_session, test_client, test_create_user, test_authenticate_user
):
    user = test_create_user(test_client)[0]
    user_pk = user.pk
    headers = _get_auth_headers(test_client, test_authenticate_user, user)

    movie = DbMovie(title="Inception", tmdb=20)
    tv = DbTVShow(title="Breaking Bad", tvmaze=30)
    game = DbVideoGame(title="Elden Ring", igdb=40)
    book = DbBook(title="Foundation", isbn="12345")
    test_db_session.add_all([movie, tv, game, book])
    test_db_session.commit()

    um = DbUserMovie(user_id=user_pk, movie_id=movie.pk, on_watchlist=True)
    utv = DbUserTVShow(user_id=user_pk, tv_show_id=tv.pk, on_watchlist=True)
    ug = DbUserVideoGame(user_id=user_pk, game_id=game.pk, on_watchlist=True)
    ub = DbUserBook(user_id=user_pk, book_id=book.pk, on_watchlist=True)
    test_db_session.add_all([um, utv, ug, ub])
    test_db_session.commit()

    res = test_client.get("/v1/users/me/bored", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["pool_size"] == 4
    assert data["pick"]["title"] in [
        "Inception",
        "Breaking Bad",
        "Elden Ring",
        "Foundation",
    ]
