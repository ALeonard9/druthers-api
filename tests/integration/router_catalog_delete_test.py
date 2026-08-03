# pylint: disable=missing-module-docstring, missing-function-docstring
"""
Deleting a catalog row must take its dependent rows with it (#227).

Before the cascades on the parent-side relationships in models_sandbox.py,
SQLAlchemy's default was to *disassociate* children — issuing
``UPDATE ... SET <parent>_id = NULL`` against FK columns declared
``nullable=False``, which the database rejected and the whole DELETE 500'd.
Every catalog domain was affected, so every one gets a regression test here.
"""

from fastapi.testclient import TestClient

from app.db.models_sandbox import (
    DbBook,
    DbMovie,
    DbTVEpisode,
    DbTVShow,
    DbUserBook,
    DbUserMovie,
    DbUserTVEpisode,
    DbUserTVShow,
    DbUserVideoGame,
    DbVideoGame,
)


def _admin(test_client: TestClient) -> dict:
    return {'Authorization': f"Bearer {test_client.admin_user.token}"}


def _user(test_client: TestClient) -> dict:
    return {'Authorization': f"Bearer {test_client.first_user.token}"}


def test_delete_movie_with_tracker_row(test_client: TestClient):
    resp = test_client.post(
        '/v1/movies',
        headers=_admin(test_client),
        json={'title': 'Inception', 'imdb': 'tt1375666'},
    )
    movie_id = resp.json()['id']
    assert (
        test_client.post(
            f"/v1/users/me/movies/{movie_id}",
            headers=_user(test_client),
            json={'on_watchlist': True},
        ).status_code
        == 201
    )

    assert (
        test_client.delete(
            f"/v1/movies/{movie_id}", headers=_admin(test_client)
        ).status_code
        == 204
    )

    db = test_client.test_db_session
    assert db.query(DbMovie).count() == 0
    assert db.query(DbUserMovie).count() == 0


def test_delete_book_with_tracker_row(test_client: TestClient):
    book_id = test_client.post(
        '/v1/books', headers=_admin(test_client), json={'title': 'Dune'}
    ).json()['id']
    assert (
        test_client.post(
            f"/v1/users/me/books/{book_id}",
            headers=_user(test_client),
            json={'on_watchlist': True},
        ).status_code
        == 201
    )

    assert (
        test_client.delete(
            f"/v1/books/{book_id}", headers=_admin(test_client)
        ).status_code
        == 204
    )

    db = test_client.test_db_session
    assert db.query(DbBook).count() == 0
    assert db.query(DbUserBook).count() == 0


def test_delete_game_with_tracker_row(test_client: TestClient):
    game_id = test_client.post(
        '/v1/games', headers=_admin(test_client), json={'title': 'Zelda'}
    ).json()['id']
    assert (
        test_client.post(
            f"/v1/users/me/games/{game_id}",
            headers=_user(test_client),
            json={'on_watchlist': True},
        ).status_code
        == 201
    )

    assert (
        test_client.delete(
            f"/v1/games/{game_id}", headers=_admin(test_client)
        ).status_code
        == 204
    )

    db = test_client.test_db_session
    assert db.query(DbVideoGame).count() == 0
    assert db.query(DbUserVideoGame).count() == 0


def test_delete_tv_show_cascades_two_levels(test_client: TestClient):
    """A show carries episodes, and each episode carries per-user watch marks."""
    show_id = test_client.post(
        '/v1/tv-shows', headers=_admin(test_client), json={'title': 'Breaking Bad'}
    ).json()['id']
    episode_id = test_client.post(
        f"/v1/tv-shows/{show_id}/episodes",
        headers=_admin(test_client),
        json={'title': 'Pilot', 'season': 1, 'season_number': 1},
    ).json()['id']
    # Both a show-level tracker row and an episode-level watch mark.
    assert (
        test_client.post(
            f"/v1/users/me/tv-shows/{show_id}",
            headers=_user(test_client),
            json={'on_watchlist': True},
        ).status_code
        == 201
    )
    test_client.post(f"/v1/users/me/episodes/{episode_id}", headers=_user(test_client))

    db = test_client.test_db_session
    assert db.query(DbUserTVEpisode).count() == 1, 'watch mark should exist first'

    assert (
        test_client.delete(
            f"/v1/tv-shows/{show_id}", headers=_admin(test_client)
        ).status_code
        == 204
    )

    assert db.query(DbTVShow).count() == 0
    assert db.query(DbUserTVShow).count() == 0
    assert db.query(DbTVEpisode).count() == 0
    # The two-levels-down rows are the ones a single cascade would have missed.
    assert db.query(DbUserTVEpisode).count() == 0


def test_delete_episode_with_watch_mark(test_client: TestClient):
    """Deleting a single episode clears its watch marks but leaves the show."""
    show_id = test_client.post(
        '/v1/tv-shows', headers=_admin(test_client), json={'title': 'Severance'}
    ).json()['id']
    episode_id = test_client.post(
        f"/v1/tv-shows/{show_id}/episodes",
        headers=_admin(test_client),
        json={'title': 'Good News About Hell', 'season': 1, 'season_number': 1},
    ).json()['id']
    test_client.post(f"/v1/users/me/episodes/{episode_id}", headers=_user(test_client))

    assert (
        test_client.delete(
            f"/v1/episodes/{episode_id}", headers=_admin(test_client)
        ).status_code
        == 204
    )

    db = test_client.test_db_session
    assert db.query(DbTVEpisode).count() == 0
    assert db.query(DbUserTVEpisode).count() == 0
    assert db.query(DbTVShow).count() == 1, 'the show itself must survive'


def test_delete_movie_leaves_other_movies_untouched(test_client: TestClient):
    """The cascade must be scoped to the deleted row, not the whole table."""
    keep_id = test_client.post(
        '/v1/movies',
        headers=_admin(test_client),
        json={'title': 'Keep Me', 'imdb': 'tt0000001'},
    ).json()['id']
    drop_id = test_client.post(
        '/v1/movies',
        headers=_admin(test_client),
        json={'title': 'Drop Me', 'imdb': 'tt0000002'},
    ).json()['id']
    for movie_id in (keep_id, drop_id):
        test_client.post(
            f"/v1/users/me/movies/{movie_id}",
            headers=_user(test_client),
            json={'on_watchlist': True},
        )

    assert (
        test_client.delete(
            f"/v1/movies/{drop_id}", headers=_admin(test_client)
        ).status_code
        == 204
    )

    db = test_client.test_db_session
    assert db.query(DbMovie).count() == 1
    assert db.query(DbUserMovie).count() == 1
    assert db.query(DbMovie).one().title == 'Keep Me'
