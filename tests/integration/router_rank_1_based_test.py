"""
Rank 0 must be impossible, from every direction.

The Top 5 board prints the stored rank rather than the row position, so a
0-based rank shows up as a literal "0" against the user's best movie. That
regressed in prod more than once because the invariant was only ever restored
by a repair script; these tests pin it at the two layers that now enforce it —
the API schema (422 before anything is written) and the database CHECK
(ck_<table>_rank_1_based, which SQLite honours too).
"""

# pylint: disable=missing-function-docstring
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from app.db.models_sandbox import (
    DbUserBook,
    DbUserMovie,
    DbUserTVShow,
    DbUserVideoGame,
)

TRACKERS = (
    (DbUserMovie, 'movie_id'),
    (DbUserTVShow, 'tv_show_id'),
    (DbUserBook, 'book_id'),
    (DbUserVideoGame, 'game_id'),
)


def _auth(token: str) -> dict:
    return {'Authorization': f'Bearer {token}'}


def _add_movie(test_client: TestClient, imdb: str) -> str:
    return test_client.post(
        '/v1/movies',
        headers=_auth(test_client.admin_user.token),
        json={'title': 'Interstellar', 'imdb': imdb, 'year': 2014},
    ).json()['id']


@pytest.mark.parametrize('model,fk', TRACKERS)
def test_database_rejects_rank_zero(test_db_session, model, fk):
    test_db_session.add(model(**{fk: 1, 'user_id': 1, 'on_rankings': True, 'rank': 0}))
    with pytest.raises(IntegrityError):
        test_db_session.flush()


@pytest.mark.parametrize('model,fk', TRACKERS)
def test_database_rejects_negative_rank(test_db_session, model, fk):
    test_db_session.add(model(**{fk: 1, 'user_id': 1, 'on_rankings': True, 'rank': -3}))
    with pytest.raises(IntegrityError):
        test_db_session.flush()


@pytest.mark.parametrize('model,fk', TRACKERS)
def test_unplaced_tracker_may_hold_no_rank(test_db_session, model, fk):
    # NULL is the "tracked but not placed" case and stays legal.
    test_db_session.add(
        model(**{fk: 1, 'user_id': 1, 'on_rankings': False, 'rank': None})
    )
    test_db_session.flush()


def test_put_tracker_with_rank_zero_is_rejected(test_client):
    token = test_client.first_user.token
    movie_id = _add_movie(test_client, 'tt-rank-zero')
    test_client.post(
        f'/v1/users/me/movies/{movie_id}',
        headers=_auth(token),
        json={'on_rankings': True},
    )

    response = test_client.put(
        f'/v1/users/me/movies/{movie_id}',
        headers=_auth(token),
        json={'rank': 0},
    )

    assert response.status_code == 422


def test_placing_at_position_zero_is_rejected(test_client):
    token = test_client.first_user.token
    movie_id = _add_movie(test_client, 'tt-position-zero')
    test_client.post(
        f'/v1/users/me/movies/{movie_id}',
        headers=_auth(token),
        json={'on_rankings': True},
    )

    response = test_client.put(
        f'/v1/users/me/movies/{movie_id}/rank',
        headers=_auth(token),
        json={'position': 0},
    )

    assert response.status_code == 422


def test_first_placement_lands_at_rank_one(test_client):
    """The regression itself: the top of the list is 1, and the board says 1."""
    token = test_client.first_user.token
    movie_id = _add_movie(test_client, 'tt-first-placement')
    test_client.post(
        f'/v1/users/me/movies/{movie_id}',
        headers=_auth(token),
        json={'on_rankings': True},
    )
    test_client.put(
        f'/v1/users/me/movies/{movie_id}/rank',
        headers=_auth(token),
        json={'position': 1},
    )

    summary = test_client.get('/v1/users/me/summary', headers=_auth(token)).json()
    movies = next(s for s in summary['shelves'] if s['category'] == 'movies')
    assert movies['top'][0]['rank'] == 1
