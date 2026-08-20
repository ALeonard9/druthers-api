# pylint: disable=missing-module-docstring, missing-function-docstring
# pylint: disable=protected-access, missing-class-docstring
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from app.config import Settings
from app.services import movie_search, tmdb

DETAIL_PAYLOAD = {
    'id': 603,
    'imdb_id': 'tt0133093',
    'title': 'The Matrix',
    'release_date': '1999-03-30',
    'runtime': 136,
    'overview': 'A hacker learns the truth.',
    'vote_average': 8.2,
    'poster_path': '/matrix.jpg',
    'original_language': 'en',
    'spoken_languages': [{'english_name': 'English', 'name': 'English'}],
    'genres': [{'name': 'Action'}, {'name': 'Science Fiction'}],
    'credits': {
        'crew': [
            {'job': 'Director', 'name': 'Lana Wachowski'},
            {'job': 'Director', 'name': 'Lilly Wachowski'},
            {'job': 'Editor', 'name': 'Zach Staenberg'},
        ],
        'cast': [
            {'name': 'Keanu Reeves'},
            {'name': 'Laurence Fishburne'},
            {'name': 'Carrie-Anne Moss'},
            {'name': 'Hugo Weaving'},
            {'name': 'Gloria Foster'},
        ],
    },
    'release_dates': {
        'results': [
            {'iso_3166_1': 'GB', 'release_dates': [{'certification': '15'}]},
            {
                'iso_3166_1': 'US',
                'release_dates': [
                    {'certification': ''},
                    {'certification': 'R'},
                ],
            },
        ]
    },
}


def _response(payload, status_code=200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.headers = {}
    resp.raise_for_status.return_value = None
    resp.json.return_value = payload
    return resp


def test_image_url_builds_and_handles_missing():
    assert tmdb.image_url('/x.jpg') == 'https://image.tmdb.org/t/p/w500/x.jpg'
    assert (
        tmdb.image_url('/x.jpg', size='w92') == 'https://image.tmdb.org/t/p/w92/x.jpg'
    )
    assert tmdb.image_url(None) is None
    assert tmdb.image_url('') is None


def test_apply_detail_truncates_and_skips_none():
    class Movie:  # simple stand-in
        director = None
        genre = None
        plot = None

    m = Movie()
    movie_search.apply_detail_to_movie(
        m, {'director': 'x' * 999, 'genre': 'Sci-Fi', 'plot': None}
    )
    assert len(m.director) == 512  # truncated to column limit
    assert m.genre == 'Sci-Fi'
    assert m.plot is None  # None values are skipped


@patch('app.services.tmdb.get_settings')
@patch('app.services.tmdb.requests.get')
def test_get_movie_detail_maps_fields(mock_get, mock_settings):
    mock_settings.return_value = Settings(tmdb_api_key='k', env='github')
    mock_get.return_value = _response(DETAIL_PAYLOAD)

    detail = movie_search.get_movie_detail(603)

    assert detail['tmdb'] == 603
    assert detail['imdb'] == 'tt0133093'
    assert detail['year'] == 1999
    assert detail['runtime'] == 136
    assert detail['genre'] == 'Action, Science Fiction'
    assert detail['director'] == 'Lana Wachowski, Lilly Wachowski'
    assert detail['language'] == 'English'
    assert detail['plot'] == 'A hacker learns the truth.'
    # TMDB's own score lands in rating_tmdb; rating_imdb is never written.
    assert detail['rating_tmdb'] == 8.2
    assert 'rating_imdb' not in detail
    assert detail['poster_url'] == 'https://image.tmdb.org/t/p/w500/matrix.jpg'
    # Cast is capped, and only Directors count as director.
    assert detail['actors'] == (
        'Keanu Reeves, Laurence Fishburne, Carrie-Anne Moss, Hugo Weaving'
    )
    # US certification wins over GB, skipping the empty entry.
    assert detail['rated'] == 'R'


@patch('app.services.tmdb.get_settings')
def test_get_movie_detail_unconfigured_returns_none(mock_settings):
    mock_settings.return_value = Settings(tmdb_api_key=None, env='github')
    assert movie_search.get_movie_detail(603) is None


@patch('app.services.tmdb.get_settings')
def test_get_movie_detail_without_id_returns_none(mock_settings):
    mock_settings.return_value = Settings(tmdb_api_key='k', env='github')
    # Rows the backfill couldn't resolve have a NULL tmdb id.
    assert movie_search.get_movie_detail(None) is None


@patch('app.services.tmdb.get_settings')
@patch('app.services.tmdb.requests.get')
def test_search_movies_by_imdb_id_returns_search_hit_shape(mock_get, mock_settings):
    mock_settings.return_value = Settings(tmdb_api_key='k', env='github')
    mock_get.return_value = _response(
        {
            'movie_results': [
                {
                    'id': 597,
                    'title': 'Titanic',
                    'release_date': '1997-11-18',
                    'poster_path': '/t.jpg',
                    'popularity': 91.2,
                }
            ]
        }
    )

    results = movie_search.search_movies('tt0120338')

    assert results == [
        {
            'tmdb': 597,
            'imdb': 'tt0120338',
            'title': 'Titanic',
            'year': '1997',
            'release_date': '1997-11-18',
            'poster_url': 'https://image.tmdb.org/t/p/w500/t.jpg',
            'type': 'movie',
            'popularity': 91.2,
        }
    ]
    # Called TMDB's /find endpoint, not the title search.
    args, kwargs = mock_get.call_args
    assert args[0].endswith('/find/tt0120338')
    assert kwargs['params']['external_source'] == 'imdb_id'


@patch('app.services.tmdb.get_settings')
@patch('app.services.tmdb.requests.get')
def test_search_movies_by_imdb_id_case_insensitive(mock_get, mock_settings):
    mock_settings.return_value = Settings(tmdb_api_key='k', env='github')
    mock_get.return_value = _response(
        {'movie_results': [{'id': 597, 'title': 'Titanic', 'poster_path': None}]}
    )

    results = movie_search.search_movies('TT0120338')

    assert len(results) == 1
    # Missing poster_path stores NULL rather than a URL that would 404.
    assert results[0]['poster_url'] is None
    assert results[0]['imdb'] == 'tt0120338'


@patch('app.services.tmdb.get_settings')
@patch('app.services.tmdb.requests.get')
def test_search_movies_by_unknown_imdb_id_returns_empty(mock_get, mock_settings):
    mock_settings.return_value = Settings(tmdb_api_key='k', env='github')
    mock_get.return_value = _response({'movie_results': []})

    assert not movie_search.search_movies('tt9999999')


@patch('app.services.tmdb.get_settings')
@patch('app.services.tmdb.requests.get')
def test_search_movies_title_query_uses_search_endpoint(mock_get, mock_settings):
    mock_settings.return_value = Settings(tmdb_api_key='k', env='github')
    mock_get.return_value = _response(
        {
            'results': [
                {
                    'id': 597,
                    'title': 'Titanic',
                    'release_date': '1997-11-18',
                    'poster_path': '/t.jpg',
                    'popularity': 91.2,
                }
            ]
        }
    )

    results = movie_search.search_movies('Titanic')

    assert results[0]['title'] == 'Titanic'
    assert results[0]['tmdb'] == 597
    # Title search carries no IMDb id - that's why tmdb is the join key.
    assert results[0]['imdb'] is None
    assert results[0]['popularity'] == 91.2
    # Full release_date (not just year) so the frontend can tell unreleased
    # titles apart and show a date instead of a rank affordance (web#180).
    assert results[0]['release_date'] == '1997-11-18'
    args, kwargs = mock_get.call_args
    assert args[0].endswith('/search/movie')
    assert kwargs['params']['query'] == 'Titanic'


@patch('app.services.tmdb.get_settings')
def test_search_movies_unconfigured_returns_503(mock_settings):
    mock_settings.return_value = Settings(tmdb_api_key=None, env='github')
    with pytest.raises(HTTPException) as exc:
        movie_search.search_movies('Titanic')
    assert exc.value.status_code == 503


@patch('app.services.tmdb.get_settings')
@patch('app.services.tmdb.requests.get')
def test_search_movies_reaches_tmdb_for_a_two_character_query(mock_get, mock_settings):
    # TMDB serves one-character queries (probed 2026-08-20, api#398), so a
    # two-letter title like Go must actually reach it.
    mock_settings.return_value = Settings(tmdb_api_key='k', env='github')
    mock_get.return_value = _response({'results': []})
    assert movie_search.search_movies('Go') == []
    mock_get.assert_called()


@patch('app.services.tmdb.requests.get')
def test_search_movies_empty_query_returns_empty_without_http(mock_get):
    assert movie_search.search_movies('   ') == []
    mock_get.assert_not_called()


@pytest.mark.parametrize('status_code', [422, 404])
@patch('app.services.tmdb.get_settings')
@patch('app.services.tmdb.requests.get')
def test_search_movies_bad_query_4xx_returns_empty(
    mock_get, mock_settings, status_code
):
    mock_settings.return_value = Settings(tmdb_api_key='k', env='github')
    response = _response({}, status_code=status_code)
    response.raise_for_status.side_effect = tmdb.requests.HTTPError(response=response)
    mock_get.return_value = response

    assert movie_search.search_movies('Titanic') == []


@pytest.mark.parametrize('status_code', [500, 401, 403])
@patch('app.services.tmdb.get_settings')
@patch('app.services.tmdb.requests.get')
def test_search_movies_operator_http_error_returns_502(
    mock_get, mock_settings, status_code
):
    mock_settings.return_value = Settings(tmdb_api_key='k', env='github')
    response = _response({}, status_code=status_code)
    response.raise_for_status.side_effect = tmdb.requests.HTTPError(response=response)
    mock_get.return_value = response

    with pytest.raises(HTTPException) as exc:
        movie_search.search_movies('Titanic')
    assert exc.value.status_code == 502


@patch('app.services.tmdb.time.sleep')
@patch('app.services.tmdb.get_settings')
@patch('app.services.tmdb.requests.get')
def test_search_movies_upstream_failure_returns_502(
    mock_get, mock_settings, mock_sleep
):
    mock_settings.return_value = Settings(tmdb_api_key='k', env='github')
    mock_get.side_effect = tmdb.requests.RequestException('boom')

    with pytest.raises(HTTPException) as exc:
        movie_search.search_movies('Titanic')
    assert exc.value.status_code == 502
    del mock_sleep


@patch('app.services.tmdb.time.sleep')
@patch('app.services.tmdb.get_settings')
@patch('app.services.tmdb.requests.get')
def test_rate_limited_request_retries_then_succeeds(
    mock_get, mock_settings, mock_sleep
):
    mock_settings.return_value = Settings(tmdb_api_key='k', env='github')
    throttled = _response({}, status_code=429)
    throttled.headers = {'Retry-After': '0.01'}
    mock_get.side_effect = [throttled, _response({'results': []})]

    assert movie_search.search_movies('Titanic') == []
    assert mock_get.call_count == 2
    # Backed off using TMDB's Retry-After rather than the default.
    mock_sleep.assert_called_once_with(0.01)


@patch('app.services.tmdb.time.sleep')
@patch('app.services.tmdb.get_settings')
@patch('app.services.tmdb.requests.get')
def test_rate_limited_request_gives_up_after_max_attempts(
    mock_get, mock_settings, mock_sleep
):
    mock_settings.return_value = Settings(tmdb_api_key='k', env='github')
    mock_get.return_value = _response({}, status_code=429)

    with pytest.raises(HTTPException) as exc:
        movie_search.search_movies('Titanic')
    assert exc.value.status_code == 502
    assert mock_get.call_count == tmdb._MAX_ATTEMPTS
    del mock_sleep


@patch('app.services.tmdb.get_settings')
@patch('app.services.tmdb.requests.get')
def test_resolve_tmdb_id(mock_get, mock_settings):
    mock_settings.return_value = Settings(tmdb_api_key='k', env='github')
    mock_get.return_value = _response({'movie_results': [{'id': 597}]})
    assert movie_search.resolve_tmdb_id('tt0120338') == 597


@patch('app.services.tmdb.get_settings')
@patch('app.services.tmdb.requests.get')
def test_resolve_tmdb_id_no_match_returns_none(mock_get, mock_settings):
    mock_settings.return_value = Settings(tmdb_api_key='k', env='github')
    mock_get.return_value = _response({'movie_results': []})
    assert movie_search.resolve_tmdb_id('tt9999999') is None
    assert movie_search.resolve_tmdb_id('') is None
