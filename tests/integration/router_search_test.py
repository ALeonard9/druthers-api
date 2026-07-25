# pylint: disable=missing-module-docstring, missing-function-docstring
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

MOVIE_HIT = [{'tmdb': 329, 'title': 'Jurassic Park', 'year': '1993'}]
SHOW_HIT = [{'tvmaze': 22, 'title': 'Jurassic Show', 'year': '2021'}]


def test_global_search_requires_auth(test_client: TestClient):
    assert test_client.get('/v1/search?q=matrix').status_code == 401


@patch('app.router.v1.router_search.search_books', return_value=[])
@patch('app.router.v1.router_search.search_games', return_value=[])
@patch('app.router.v1.router_search.search_tv_shows', return_value=SHOW_HIT)
@patch('app.router.v1.router_search.search_movies', return_value=MOVIE_HIT)
def test_global_search_groups_by_domain(
    _movies, _tv, _games, _books, test_client: TestClient
):
    headers = {'Authorization': f"Bearer {test_client.first_user.token}"}
    resp = test_client.get('/v1/search?q=jurassic', headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data['query'] == 'jurassic'
    assert data['corrected'] is None
    assert data['movies'][0]['title'] == 'Jurassic Park'
    assert data['tv_shows'][0]['title'] == 'Jurassic Show'
    assert data['games'] == []
    assert data['books'] == []


@patch('app.router.v1.router_search.search_books', return_value=[])
@patch('app.router.v1.router_search.search_games', return_value=[])
@patch('app.router.v1.router_search.search_tv_shows', return_value=[])
@patch('app.router.v1.router_search.search_movies')
def test_global_search_retries_with_spelling_fix(
    mock_movies, _tv, _games, _books, test_client: TestClient
):
    mock_movies.side_effect = [[], MOVIE_HIT]
    headers = {'Authorization': f"Bearer {test_client.first_user.token}"}
    resp = test_client.get('/v1/search?q=jurrasic', headers=headers)
    data = resp.json()
    assert data['corrected'] == 'jurassic'
    assert data['movies'][0]['title'] == 'Jurassic Park'
    assert mock_movies.call_args_list[1].args[0] == 'jurassic'


@patch('app.router.v1.router_search.search_books', return_value=[])
@patch('app.router.v1.router_search.search_games', return_value=[])
@patch('app.router.v1.router_search.search_tv_shows', return_value=[])
@patch('app.router.v1.router_search.search_movies', return_value=MOVIE_HIT)
def test_failing_provider_does_not_break_search(
    _movies, _tv, _games, mock_books, test_client: TestClient
):
    mock_books.side_effect = HTTPException(status_code=503, detail='no key')
    headers = {'Authorization': f"Bearer {test_client.first_user.token}"}
    resp = test_client.get('/v1/search?q=jurassic', headers=headers)
    assert resp.status_code == 200
    assert resp.json()['movies'][0]['title'] == 'Jurassic Park'
    assert resp.json()['books'] == []


TITANIC_HITS = [
    {'tmdb': 1, 'title': 'Raise the Titanic', 'year': '1980'},
    {'tmdb': 2, 'title': 'Titanic II', 'year': '2010'},
    {'tmdb': 3, 'title': 'The Legend of the Titanic', 'year': '1999'},
    {'tmdb': 4, 'title': 'Titanic', 'year': '1997'},
    {'tmdb': 5, 'title': 'Titanica', 'year': '1992'},
    {'tmdb': 6, 'title': 'Titanic: Blood and Steel', 'year': '2012'},
]


@patch('app.router.v1.router_search.search_books', return_value=[])
@patch('app.router.v1.router_search.search_games', return_value=[])
@patch('app.router.v1.router_search.search_tv_shows', return_value=[])
@patch('app.router.v1.router_search.search_movies', return_value=TITANIC_HITS)
def test_global_search_ranks_best_match_first_and_caps_to_five(
    _movies, _tv, _games, _books, test_client: TestClient
):
    headers = {'Authorization': f"Bearer {test_client.first_user.token}"}
    resp = test_client.get('/v1/search?q=Titanic', headers=headers)
    assert resp.status_code == 200
    movies = resp.json()['movies']
    # 6 hits came back from the provider; only the top 5 are returned.
    assert len(movies) == 5
    # The exact title match ("Titanic") outranks every partial match.
    assert movies[0]['title'] == 'Titanic'
