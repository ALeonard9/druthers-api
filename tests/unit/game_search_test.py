# pylint: disable=missing-module-docstring, missing-function-docstring
# pylint: disable=protected-access, missing-class-docstring
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from app.config import Settings
from app.services import game_search


def _reset_token_cache():
    game_search._token_cache = (None, 0.0)


def test_helpers():
    assert game_search._cover({'cover': {'image_id': 'abc'}}) == (
        'https://images.igdb.com/igdb/image/upload/t_cover_big_2x/abc.jpg'
    )
    assert game_search._cover({}) is None
    assert game_search._release({'first_release_date': 1496275200}).year == 2017
    assert game_search._release({}) is None
    assert (
        game_search._names(
            {'platforms': [{'abbreviation': 'PS5'}, {'abbreviation': 'PC'}]},
            'platforms',
            'abbreviation',
        )
        == 'PS5, PC'
    )
    assert game_search._names({}, 'genres') is None


def test_apply_detail_truncates_and_skips_none():
    class Game:
        genre = None
        platforms = None
        summary = None

    g = Game()
    game_search.apply_detail_to_game(
        g, {'genre': 'x' * 999, 'platforms': 'PC', 'summary': None}
    )
    assert len(g.genre) == 255  # truncated to column limit
    assert g.platforms == 'PC'
    assert g.summary is None  # None values are skipped


@patch('app.services.game_search.get_settings')
def test_search_unconfigured_returns_503(mock_settings):
    _reset_token_cache()
    mock_settings.return_value = Settings(
        twitch_client_id=None, twitch_client_secret=None, env='github'
    )
    with pytest.raises(HTTPException) as exc:
        game_search.search_games('zelda')
    assert exc.value.status_code == 503


@patch('app.services.game_search.get_settings')
def test_get_game_detail_unconfigured_returns_none(mock_settings):
    _reset_token_cache()
    mock_settings.return_value = Settings(
        twitch_client_id=None, twitch_client_secret=None, env='github'
    )
    assert game_search.get_game_detail(1234) is None
    assert game_search.get_game_detail(None) is None


@patch('app.services.game_search.get_settings')
def test_get_time_to_beat_unconfigured_returns_none(mock_settings):
    _reset_token_cache()
    mock_settings.return_value = Settings(
        twitch_client_id=None, twitch_client_secret=None, env='github'
    )
    assert game_search.get_time_to_beat(1234) is None
    assert game_search.get_time_to_beat(None) is None


@patch('app.services.game_search.get_settings')
@patch('app.services.game_search.requests.post')
def test_search_games_returns_results(mock_post, mock_settings):
    _reset_token_cache()
    mock_settings.return_value = Settings(
        twitch_client_id='cid', twitch_client_secret='secret', env='github'
    )
    token_resp = MagicMock()
    token_resp.raise_for_status.return_value = None
    token_resp.json.return_value = {'access_token': 'tok', 'expires_in': 5000000}
    games_resp = MagicMock()
    games_resp.raise_for_status.return_value = None
    games_resp.json.return_value = [
        {
            'id': 1234,
            'name': 'The Legend of Zelda: Breath of the Wild',
            'first_release_date': 1488499200,
            'platforms': [{'abbreviation': 'Switch'}, {'abbreviation': 'WiiU'}],
            'cover': {'image_id': 'co3p2d'},
        }
    ]
    mock_post.side_effect = [token_resp, games_resp]

    results = game_search.search_games('zelda')
    assert len(results) == 1
    assert results[0]['igdb'] == 1234
    assert results[0]['title'].startswith('The Legend of Zelda')
    assert results[0]['year'] == '2017'
    assert results[0]['platforms'] == 'Switch, WiiU'
    assert results[0]['poster_url'].endswith('/co3p2d.jpg')
    _reset_token_cache()


@patch('app.services.game_search.get_settings')
@patch('app.services.game_search.requests.post')
def test_search_games_numeric_query_resolves_by_id(mock_post, mock_settings):
    _reset_token_cache()
    mock_settings.return_value = Settings(
        twitch_client_id='cid', twitch_client_secret='secret', env='github'
    )
    token_resp = MagicMock()
    token_resp.raise_for_status.return_value = None
    token_resp.json.return_value = {'access_token': 'tok', 'expires_in': 5000000}
    id_resp = MagicMock()
    id_resp.raise_for_status.return_value = None
    id_resp.json.return_value = [
        {
            'id': 1234,
            'name': 'The Legend of Zelda: Breath of the Wild',
            'slug': 'the-legend-of-zelda-breath-of-the-wild',
            'first_release_date': 1488499200,
            'platforms': [{'abbreviation': 'Switch'}, {'abbreviation': 'WiiU'}],
            'cover': {'image_id': 'co3p2d'},
        }
    ]
    mock_post.side_effect = [token_resp, id_resp]

    results = game_search.search_games('1234')

    assert len(results) == 1
    assert results[0]['igdb'] == 1234
    assert results[0]['title'] == 'The Legend of Zelda: Breath of the Wild'
    assert results[0]['slug'] == 'the-legend-of-zelda-breath-of-the-wild'
    assert results[0]['year'] == '2017'
    assert results[0]['platforms'] == 'Switch, WiiU'
    assert results[0]['poster_url'].endswith('/co3p2d.jpg')

    # The games query used a direct id lookup, not a fuzzy title search.
    games_call = mock_post.call_args_list[1]
    assert 'where id = 1234' in games_call.kwargs['data']
    assert 'search ' not in games_call.kwargs['data']
    _reset_token_cache()


@patch('app.services.game_search.get_settings')
@patch('app.services.game_search.requests.post')
def test_search_games_numeric_query_unknown_id_returns_empty(mock_post, mock_settings):
    _reset_token_cache()
    mock_settings.return_value = Settings(
        twitch_client_id='cid', twitch_client_secret='secret', env='github'
    )
    token_resp = MagicMock()
    token_resp.raise_for_status.return_value = None
    token_resp.json.return_value = {'access_token': 'tok', 'expires_in': 5000000}
    id_resp = MagicMock()
    id_resp.raise_for_status.return_value = None
    id_resp.json.return_value = []
    mock_post.side_effect = [token_resp, id_resp]

    assert not game_search.search_games('999999999')
    _reset_token_cache()


@patch('app.services.game_search.get_settings')
@patch('app.services.game_search.requests.post')
def test_search_games_title_query_still_uses_fuzzy_search(mock_post, mock_settings):
    _reset_token_cache()
    mock_settings.return_value = Settings(
        twitch_client_id='cid', twitch_client_secret='secret', env='github'
    )
    token_resp = MagicMock()
    token_resp.raise_for_status.return_value = None
    token_resp.json.return_value = {'access_token': 'tok', 'expires_in': 5000000}
    games_resp = MagicMock()
    games_resp.raise_for_status.return_value = None
    games_resp.json.return_value = []
    mock_post.side_effect = [token_resp, games_resp]

    game_search.search_games('zelda')

    games_call = mock_post.call_args_list[1]
    assert 'search "zelda"' in games_call.kwargs['data']
    assert 'where id' not in games_call.kwargs['data']
    _reset_token_cache()


@patch('app.services.game_search.get_settings')
@patch('app.services.game_search.requests.post')
def test_get_game_detail_maps_fields_and_caches_token(mock_post, mock_settings):
    _reset_token_cache()
    mock_settings.return_value = Settings(
        twitch_client_id='cid', twitch_client_secret='secret', env='github'
    )
    token_resp = MagicMock()
    token_resp.raise_for_status.return_value = None
    token_resp.json.return_value = {'access_token': 'tok', 'expires_in': 5000000}
    detail_resp = MagicMock()
    detail_resp.raise_for_status.return_value = None
    detail_resp.json.return_value = [
        {
            'id': 1234,
            'name': 'Breath of the Wild',
            'slug': 'the-legend-of-zelda-breath-of-the-wild',
            'first_release_date': 1488499200,
            'total_rating': 92.456,
            'genres': [{'name': 'Adventure'}, {'name': 'RPG'}],
            'platforms': [{'abbreviation': 'Switch'}],
            'summary': 'Open-air adventure.',
            'cover': {'image_id': 'co3p2d'},
            'updated_at': 1600000000,
        }
    ]
    second_detail = MagicMock()
    second_detail.raise_for_status.return_value = None
    second_detail.json.return_value = []
    mock_post.side_effect = [token_resp, detail_resp, second_detail]

    detail = game_search.get_game_detail(1234)
    assert detail['title'] == 'Breath of the Wild'
    assert detail['year'] == 2017
    assert detail['genre'] == 'Adventure, RPG'
    assert detail['platforms'] == 'Switch'
    assert detail['rating'] == 92.5
    assert detail['summary'] == 'Open-air adventure.'
    assert detail['poster_url'].endswith('/co3p2d.jpg')
    assert detail['igdb_last_update'].year == 2020

    # Second call reuses the cached token (no extra OAuth POST).
    assert game_search.get_game_detail(9999) is None
    assert mock_post.call_count == 3  # 1 token + 2 queries
    _reset_token_cache()


@patch('app.services.game_search.get_settings')
@patch('app.services.game_search.requests.post')
def test_get_time_to_beat_converts_seconds_to_hours(mock_post, mock_settings):
    _reset_token_cache()
    mock_settings.return_value = Settings(
        twitch_client_id='cid', twitch_client_secret='secret', env='github'
    )
    token_resp = MagicMock()
    token_resp.raise_for_status.return_value = None
    token_resp.json.return_value = {'access_token': 'tok', 'expires_in': 5000000}
    ttb_resp = MagicMock()
    ttb_resp.raise_for_status.return_value = None
    ttb_resp.json.return_value = [{'game_id': 1234, 'normally': 61200}]  # 17h
    mock_post.side_effect = [token_resp, ttb_resp]

    assert game_search.get_time_to_beat(1234) == 17
    query_call = mock_post.call_args_list[1]
    assert 'where game_id = 1234' in query_call.kwargs['data']
    _reset_token_cache()


@patch('app.services.game_search.get_settings')
@patch('app.services.game_search.requests.post')
def test_get_time_to_beat_no_community_data_returns_none(mock_post, mock_settings):
    _reset_token_cache()
    mock_settings.return_value = Settings(
        twitch_client_id='cid', twitch_client_secret='secret', env='github'
    )
    token_resp = MagicMock()
    token_resp.raise_for_status.return_value = None
    token_resp.json.return_value = {'access_token': 'tok', 'expires_in': 5000000}
    ttb_resp = MagicMock()
    ttb_resp.raise_for_status.return_value = None
    ttb_resp.json.return_value = []
    mock_post.side_effect = [token_resp, ttb_resp]

    assert game_search.get_time_to_beat(1234) is None
    _reset_token_cache()


@patch('app.services.game_search.get_settings')
@patch('app.services.game_search.requests.post')
def test_search_games_reaches_igdb_for_a_two_character_query(mock_post, mock_settings):
    # IGDB serves one-character queries (probed 2026-08-20, api#398).
    _reset_token_cache()
    mock_settings.return_value = Settings(
        twitch_client_id='cid', twitch_client_secret='secret', env='github'
    )
    token_resp = MagicMock()
    token_resp.raise_for_status.return_value = None
    token_resp.json.return_value = {'access_token': 'tok', 'expires_in': 5000000}
    games_resp = MagicMock()
    games_resp.raise_for_status.return_value = None
    games_resp.json.return_value = []
    mock_post.side_effect = [token_resp, games_resp]

    assert not game_search.search_games('Go')
    assert mock_post.call_count == 2
    _reset_token_cache()


@patch('app.services.game_search.requests.post')
def test_search_games_empty_query_returns_empty_without_http(mock_post):
    assert not game_search.search_games('   ')
    mock_post.assert_not_called()


@pytest.mark.parametrize('status_code', [422, 404])
@patch('app.services.game_search.get_settings')
@patch('app.services.game_search.requests.post')
def test_search_games_bad_query_4xx_returns_empty(
    mock_post, mock_settings, status_code
):
    _reset_token_cache()
    mock_settings.return_value = Settings(
        twitch_client_id='cid', twitch_client_secret='secret', env='github'
    )
    token_response = MagicMock()
    token_response.raise_for_status.return_value = None
    token_response.json.return_value = {'access_token': 'tok', 'expires_in': 5000000}
    query_response = MagicMock(status_code=status_code)
    query_response.raise_for_status.side_effect = game_search.requests.HTTPError(
        response=query_response
    )
    mock_post.side_effect = [token_response, query_response]

    assert not game_search.search_games('Zelda')
    _reset_token_cache()


@pytest.mark.parametrize('status_code', [500, 403])
@patch('app.services.game_search.get_settings')
@patch('app.services.game_search.requests.post')
def test_search_games_operator_http_error_returns_502(
    mock_post, mock_settings, status_code
):
    _reset_token_cache()
    mock_settings.return_value = Settings(
        twitch_client_id='cid', twitch_client_secret='secret', env='github'
    )
    token_response = MagicMock()
    token_response.raise_for_status.return_value = None
    token_response.json.return_value = {'access_token': 'tok', 'expires_in': 5000000}
    query_response = MagicMock(status_code=status_code)
    query_response.raise_for_status.side_effect = game_search.requests.HTTPError(
        response=query_response
    )
    mock_post.side_effect = [token_response, query_response]

    with pytest.raises(HTTPException) as exc:
        game_search.search_games('Zelda')
    assert exc.value.status_code == 502
    _reset_token_cache()


@patch('app.services.game_search.get_settings')
@patch('app.services.game_search.requests.post')
def test_search_games_repeated_401_returns_502(mock_post, mock_settings):
    _reset_token_cache()
    mock_settings.return_value = Settings(
        twitch_client_id='cid', twitch_client_secret='secret', env='github'
    )

    def token_response(value):
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = {'access_token': value, 'expires_in': 5000000}
        return response

    def unauthorized_response():
        response = MagicMock(status_code=401)
        response.raise_for_status.side_effect = game_search.requests.HTTPError(
            response=response
        )
        return response

    mock_post.side_effect = [
        token_response('first'),
        unauthorized_response(),
        token_response('second'),
        unauthorized_response(),
    ]

    with pytest.raises(HTTPException) as exc:
        game_search.search_games('Zelda')
    assert exc.value.status_code == 502
    _reset_token_cache()
