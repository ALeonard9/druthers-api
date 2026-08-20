# pylint: disable=missing-module-docstring, missing-function-docstring
# pylint: disable=protected-access, missing-class-docstring
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from app.services import tv_search


def test_strip_html_and_to_date():
    assert (
        tv_search._strip_html('<p>Mark <b>Scout</b> leads a team.</p>')
        == 'Mark Scout leads a team.'
    )
    assert tv_search._strip_html('') is None
    assert tv_search._strip_html(None) is None
    assert tv_search._to_date('2022-02-18').year == 2022
    assert tv_search._to_date('not-a-date') is None
    assert tv_search._to_date(None) is None


def test_tvmaze_headers_identify_druthers():
    assert tv_search.TVMAZE_HEADERS == {'User-Agent': 'druthers.io (Admin@druthers.io)'}


def test_apply_detail_truncates_and_skips_none():
    class Show:
        genre = None
        language = None
        summary = None

    s = Show()
    tv_search.apply_detail_to_show(
        s, {'genre': 'x' * 999, 'language': 'English', 'summary': None}
    )
    assert len(s.genre) == 255  # truncated to column limit
    assert s.language == 'English'
    assert s.summary is None  # None values are skipped


@patch('app.services.tv_search.requests.get')
def test_get_tv_show_detail_maps_fields(mock_get):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {
        'id': 44932,
        'name': 'Severance',
        'status': 'Running',
        'premiered': '2022-02-18',
        'genres': ['Drama', 'Science-Fiction', 'Thriller'],
        'language': 'English',
        'averageRuntime': 50,
        'rating': {'average': 8.7},
        'network': None,
        'webChannel': {'name': 'Apple TV+'},
        'externals': {'imdb': 'tt11280740'},
        'image': {'medium': 'https://x/m.jpg', 'original': 'https://x/o.jpg'},
        'summary': '<p>Mark leads a team of severed employees.</p>',
    }
    mock_get.return_value = resp

    detail = tv_search.get_tv_show_detail(44932)
    mock_get.assert_called_once_with(
        'https://api.tvmaze.com/shows/44932',
        timeout=tv_search.REQUEST_TIMEOUT,
        headers=tv_search.TVMAZE_HEADERS,
    )
    assert detail['title'] == 'Severance'
    assert detail['imdb'] == 'tt11280740'
    assert detail['status'] == 'Running'
    assert detail['year'] == 2022
    assert detail['genre'] == 'Drama, Science-Fiction, Thriller'
    assert detail['network'] == 'Apple TV+'
    assert detail['runtime'] == 50
    assert detail['rating'] == 8.7
    assert detail['summary'] == 'Mark leads a team of severed employees.'
    assert detail['poster_url'] == 'https://x/o.jpg'


def test_get_tv_show_detail_without_id_returns_none():
    assert tv_search.get_tv_show_detail(None) is None


@patch('app.services.tv_search.requests.get')
def test_get_show_episodes_normalizes(mock_get):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = [
        {
            'id': 2128885,
            'name': 'Good News About Hell',
            'season': 1,
            'number': 1,
            'airdate': '2022-02-18',
        },
        {'id': 2128886, 'name': None, 'season': 1, 'number': 2, 'airdate': ''},
    ]
    mock_get.return_value = resp

    episodes = tv_search.get_show_episodes(44932)
    mock_get.assert_called_once_with(
        'https://api.tvmaze.com/shows/44932/episodes',
        timeout=tv_search.REQUEST_TIMEOUT,
        headers=tv_search.TVMAZE_HEADERS,
    )
    assert len(episodes) == 2
    assert episodes[0]['tvmaze'] == 2128885
    assert episodes[0]['title'] == 'Good News About Hell'
    assert episodes[0]['season'] == 1
    assert episodes[0]['season_number'] == 1
    assert episodes[0]['airdate'].year == 2022
    # Missing name falls back; missing airdate stays None.
    assert episodes[1]['title'] == 'Untitled'
    assert episodes[1]['airdate'] is None


def test_get_show_episodes_without_id_returns_empty():
    assert not tv_search.get_show_episodes(None)


def _tvmaze_show(**overrides):
    show = {
        'id': 44932,
        'name': 'Severance',
        'status': 'Running',
        'premiered': '2022-02-18',
        'network': {'name': 'Apple TV+'},
        'externals': {'imdb': 'tt11280740'},
        'image': {'medium': 'https://x/m.jpg', 'original': 'https://x/o.jpg'},
    }
    show.update(overrides)
    return show


@patch('app.services.tv_search.requests.get')
def test_search_tv_shows_imdb_id_uses_lookup(mock_get):
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status.return_value = None
    resp.json.return_value = _tvmaze_show()
    mock_get.return_value = resp

    results = tv_search.search_tv_shows('tt11280740')

    mock_get.assert_called_once_with(
        'https://api.tvmaze.com/lookup/shows',
        params={'imdb': 'tt11280740'},
        timeout=tv_search.REQUEST_TIMEOUT,
        headers=tv_search.TVMAZE_HEADERS,
    )
    assert len(results) == 1
    assert results[0] == {
        'tvmaze': 44932,
        'imdb': 'tt11280740',
        'title': 'Severance',
        'year': '2022',
        'status': 'Running',
        'network': 'Apple TV+',
        'poster_url': 'https://x/o.jpg',
    }


@patch('app.services.tv_search.requests.get')
def test_search_tv_shows_imdb_id_is_case_insensitive(mock_get):
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status.return_value = None
    resp.json.return_value = _tvmaze_show()
    mock_get.return_value = resp

    tv_search.search_tv_shows('TT11280740')

    mock_get.assert_called_once_with(
        'https://api.tvmaze.com/lookup/shows',
        params={'imdb': 'tt11280740'},
        timeout=tv_search.REQUEST_TIMEOUT,
        headers=tv_search.TVMAZE_HEADERS,
    )


@patch('app.services.tv_search.requests.get')
def test_search_tv_shows_thetvdb_id_uses_lookup(mock_get):
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status.return_value = None
    resp.json.return_value = _tvmaze_show()
    mock_get.return_value = resp

    results = tv_search.search_tv_shows('281588')

    mock_get.assert_called_once_with(
        'https://api.tvmaze.com/lookup/shows',
        params={'thetvdb': '281588'},
        timeout=tv_search.REQUEST_TIMEOUT,
        headers=tv_search.TVMAZE_HEADERS,
    )
    assert results[0]['title'] == 'Severance'


@patch('app.services.tv_search.requests.get')
def test_search_tv_shows_unknown_id_returns_empty_list(mock_get):
    resp = MagicMock()
    resp.status_code = 404
    mock_get.return_value = resp

    assert not tv_search.search_tv_shows('tt00000000')
    assert not tv_search.search_tv_shows('999999')


@patch('app.services.tv_search.requests.get')
def test_search_tv_shows_short_numeric_query_falls_back_to_title_search(
    mock_get,
):
    """A 4-digit numeric query (e.g. the show "1923") is treated as a title,
    not a TheTVDB id -- avoids the common short-numeric-title collision."""
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = [{'show': _tvmaze_show(name='1923')}]
    mock_get.return_value = resp

    results = tv_search.search_tv_shows('1923')

    mock_get.assert_called_once_with(
        'https://api.tvmaze.com/search/shows',
        params={'q': '1923'},
        timeout=tv_search.REQUEST_TIMEOUT,
        headers=tv_search.TVMAZE_HEADERS,
    )
    assert results[0]['title'] == '1923'


@patch('app.services.tv_search.requests.get')
def test_search_tv_shows_ordinary_title_query_unaffected(mock_get):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = [{'show': _tvmaze_show()}]
    mock_get.return_value = resp

    results = tv_search.search_tv_shows('Severance')

    mock_get.assert_called_once_with(
        'https://api.tvmaze.com/search/shows',
        params={'q': 'Severance'},
        timeout=tv_search.REQUEST_TIMEOUT,
        headers=tv_search.TVMAZE_HEADERS,
    )
    assert results[0]['title'] == 'Severance'


@patch('app.services.tv_search.requests.get')
def test_search_tv_shows_reaches_tvmaze_for_a_two_character_query(mock_get):
    # TVMaze serves one-character queries (probed 2026-08-20, api#398).
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = []
    mock_get.return_value = resp
    assert not tv_search.search_tv_shows('Go')
    mock_get.assert_called()


@patch('app.services.tv_search.requests.get')
def test_search_tv_shows_empty_query_returns_empty_without_http(mock_get):
    assert not tv_search.search_tv_shows('   ')
    mock_get.assert_not_called()


@pytest.mark.parametrize('status_code', [422, 404])
@patch('app.services.tv_search.requests.get')
def test_search_tv_shows_bad_query_4xx_returns_empty(mock_get, status_code):
    response = MagicMock(status_code=status_code)
    response.raise_for_status.side_effect = tv_search.requests.HTTPError(
        response=response
    )
    mock_get.return_value = response

    assert not tv_search.search_tv_shows('Severance')


@pytest.mark.parametrize('status_code', [500, 401, 403])
@patch('app.services.tv_search.requests.get')
def test_search_tv_shows_operator_http_error_returns_502(mock_get, status_code):
    response = MagicMock(status_code=status_code)
    response.raise_for_status.side_effect = tv_search.requests.HTTPError(
        response=response
    )
    mock_get.return_value = response

    with pytest.raises(HTTPException) as exc:
        tv_search.search_tv_shows('Severance')
    assert exc.value.status_code == 502
