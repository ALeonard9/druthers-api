# pylint: disable=missing-module-docstring, missing-function-docstring
"""
Unit tests for app.services.tracked_status.

Verifies attaching user tracking state (on_watchlist, on_rankings, rank) to search
provider results across all four domains (movies, tv_shows, games, books).
Satisfies issue druthers-api#290.
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
from app.services.tracked_status import attach_tracked_status


def test_attach_tracked_status_movies(test_db_session, test_client, test_create_user):
    user = test_create_user(test_client)[0]
    user_pk = user.pk

    # Setup catalog movie with TMDB id (Integer)
    movie = DbMovie(title='Inception', year='2010', tmdb=27205)
    test_db_session.add(movie)
    test_db_session.commit()

    # User tracks movie on rankings
    user_movie = DbUserMovie(
        user_id=user_pk,
        movie_id=movie.pk,
        on_watchlist=False,
        on_rankings=True,
        rank=1,
    )
    test_db_session.add(user_movie)
    test_db_session.commit()

    results = [
        {'tmdb': 27205, 'title': 'Inception'},
        {'tmdb': 99999, 'title': 'Untracked Movie'},
        {'title': 'Missing TMDB ID'},
    ]

    res = attach_tracked_status(test_db_session, user_pk, results, 'movies')

    assert res[0]['on_watchlist'] is False
    assert res[0]['on_rankings'] is True
    assert res[0]['rank'] == 1

    assert res[1]['on_watchlist'] is False
    assert res[1]['on_rankings'] is False
    assert res[1]['rank'] is None

    assert res[2]['on_watchlist'] is False
    assert res[2]['on_rankings'] is False
    assert res[2]['rank'] is None


def test_attach_tracked_status_tv_shows(test_db_session, test_client, test_create_user):
    user = test_create_user(test_client)[0]
    user_pk = user.pk

    tv = DbTVShow(title='Breaking Bad', year='2008', tvmaze=169)
    test_db_session.add(tv)
    test_db_session.commit()

    user_tv = DbUserTVShow(
        user_id=user_pk,
        tv_show_id=tv.pk,
        on_watchlist=True,
        on_rankings=False,
    )
    test_db_session.add(user_tv)
    test_db_session.commit()

    results = [{'tvmaze': 169, 'title': 'Breaking Bad'}]
    res = attach_tracked_status(test_db_session, user_pk, results, 'tv_shows')

    assert res[0]['on_watchlist'] is True
    assert res[0]['on_rankings'] is False
    assert res[0]['rank'] is None


def test_attach_tracked_status_games(test_db_session, test_client, test_create_user):
    user = test_create_user(test_client)[0]
    user_pk = user.pk

    game = DbVideoGame(title='Elden Ring', year='2022', igdb=119133)
    test_db_session.add(game)
    test_db_session.commit()

    user_game = DbUserVideoGame(
        user_id=user_pk,
        game_id=game.pk,
        on_watchlist=False,
        on_rankings=True,
        rank=3,
    )
    test_db_session.add(user_game)
    test_db_session.commit()

    results = [{'igdb': 119133, 'title': 'Elden Ring'}]
    res = attach_tracked_status(test_db_session, user_pk, results, 'games')

    assert res[0]['on_watchlist'] is False
    assert res[0]['on_rankings'] is True
    assert res[0]['rank'] == 3


def test_attach_tracked_status_books(test_db_session, test_client, test_create_user):
    user = test_create_user(test_client)[0]
    user_pk = user.pk

    book = DbBook(title='Dune', isbn='9780441172719')
    test_db_session.add(book)
    test_db_session.commit()

    user_book = DbUserBook(
        user_id=user_pk,
        book_id=book.pk,
        on_watchlist=True,
        on_rankings=True,
        rank=5,
    )
    test_db_session.add(user_book)
    test_db_session.commit()

    results = [{'isbn': '9780441172719', 'title': 'Dune'}]
    res = attach_tracked_status(test_db_session, user_pk, results, 'books')

    assert res[0]['on_watchlist'] is True
    assert res[0]['on_rankings'] is True
    assert res[0]['rank'] == 5


def test_attach_tracked_status_empty_results(test_db_session):
    res = attach_tracked_status(test_db_session, 1, [], 'movies')
    assert not res
