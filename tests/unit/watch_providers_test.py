# pylint: disable=missing-module-docstring, missing-function-docstring
# pylint: disable=protected-access
from unittest.mock import patch

import pytest

from app.services import watch_providers

MOVIE_PAYLOAD = {
    'id': 603,
    'results': {
        'US': {
            'link': 'https://www.themoviedb.org/movie/603/watch?locale=US',
            'flatrate': [
                {
                    'provider_id': 8,
                    'provider_name': 'Netflix',
                    'logo_path': '/netflix.jpg',
                    'display_priority': 3,
                },
                {
                    'provider_id': 1899,
                    'provider_name': 'Max',
                    'logo_path': '/max.jpg',
                    'display_priority': 1,
                },
            ],
            'free': [
                {
                    'provider_id': 73,
                    'provider_name': 'Tubi TV',
                    'logo_path': '/tubi.jpg',
                    'display_priority': 5,
                }
            ],
            'ads': [
                {
                    'provider_id': 73,
                    'provider_name': 'Tubi TV',
                    'logo_path': '/tubi.jpg',
                    'display_priority': 5,
                },
                {
                    'provider_id': 300,
                    'provider_name': 'Pluto TV',
                    'logo_path': '/pluto.jpg',
                    'display_priority': 2,
                },
            ],
            'rent': [
                {
                    'provider_id': 2,
                    'provider_name': 'Apple TV',
                    'logo_path': '/apple.jpg',
                    'display_priority': 0,
                }
            ],
            'buy': [
                {
                    'provider_id': 10,
                    'provider_name': 'Amazon Video',
                    'logo_path': '/amazon.jpg',
                    'display_priority': 0,
                }
            ],
        },
        'GB': {
            'link': 'https://www.themoviedb.org/movie/603/watch?locale=GB',
            'flatrate': [
                {
                    'provider_id': 39,
                    'provider_name': 'Now TV',
                    'logo_path': '/now.jpg',
                    'display_priority': 0,
                }
            ],
        },
    },
}


@pytest.fixture(autouse=True)
def _cold_cache():
    """Each test starts with no memoized lookups."""
    watch_providers.reset_cache()
    yield
    watch_providers.reset_cache()


@patch('app.services.tmdb.try_request')
def test_movie_providers_group_and_order(mock_request):
    mock_request.return_value = MOVIE_PAYLOAD

    result = watch_providers.get_movie_providers(603)

    mock_request.assert_called_once_with('/movie/603/watch/providers')
    assert result['region'] == 'US'
    assert result['link'] == 'https://www.themoviedb.org/movie/603/watch?locale=US'
    assert result['attribution'] == 'JustWatch'
    # Ordered by TMDB's display_priority, not payload order.
    assert [p['name'] for p in result['stream']] == ['Max', 'Netflix']
    assert result['stream'][0]['logo_url'] == 'https://image.tmdb.org/t/p/w92/max.jpg'
    assert result['stream'][0]['provider_id'] == 1899
    assert [p['name'] for p in result['rent']] == ['Apple TV']
    assert [p['name'] for p in result['buy']] == ['Amazon Video']


@patch('app.services.tmdb.try_request')
def test_ads_fold_into_free_without_duplicates(mock_request):
    mock_request.return_value = MOVIE_PAYLOAD

    result = watch_providers.get_movie_providers(603)

    # Tubi is listed under both free and ads; it appears once, and Pluto's
    # lower display_priority puts it first.
    assert [p['name'] for p in result['free']] == ['Pluto TV', 'Tubi TV']


@patch('app.services.tmdb.try_request')
def test_region_selects_a_different_block(mock_request):
    mock_request.return_value = MOVIE_PAYLOAD

    result = watch_providers.get_movie_providers(603, region='gb')

    assert result['region'] == 'GB'
    assert [p['name'] for p in result['stream']] == ['Now TV']


@patch('app.services.tmdb.try_request')
def test_unknown_region_falls_back_to_us(mock_request):
    mock_request.return_value = MOVIE_PAYLOAD

    # A junk region degrades to US availability rather than 400ing.
    assert watch_providers.get_movie_providers(603, region='nonsense')['region'] == 'US'
    assert watch_providers.get_movie_providers(603, region='')['region'] == 'US'
    # A well-formed region TMDB has no data for is empty, not an error.
    empty = watch_providers.get_movie_providers(603, region='JP')
    assert empty['region'] == 'JP'
    assert empty['stream'] == []
    assert empty['link'] is None


@patch('app.services.tmdb.try_request')
def test_movie_without_tmdb_id_skips_the_call(mock_request):
    # Rows the backfill couldn't resolve have a NULL tmdb id.
    result = watch_providers.get_movie_providers(None)

    mock_request.assert_not_called()
    assert result == {
        'region': 'US',
        'link': None,
        'attribution': 'JustWatch',
        'stream': [],
        'free': [],
        'rent': [],
        'buy': [],
    }


@patch('app.services.tmdb.try_request')
def test_upstream_failure_returns_empty_buckets(mock_request):
    # try_request returns None when TMDB is unreachable or unconfigured.
    mock_request.return_value = None

    result = watch_providers.get_movie_providers(603)

    assert result['stream'] == []
    assert result['link'] is None


@patch('app.services.tmdb.try_request')
def test_providers_are_cached_per_title_and_region(mock_request):
    mock_request.return_value = MOVIE_PAYLOAD

    watch_providers.get_movie_providers(603)
    watch_providers.get_movie_providers(603)
    assert mock_request.call_count == 1

    # A different region is a different cache key.
    watch_providers.get_movie_providers(603, region='GB')
    assert mock_request.call_count == 2

    watch_providers.reset_cache()
    watch_providers.get_movie_providers(603)
    assert mock_request.call_count == 3


@patch('app.services.tmdb.try_request')
def test_expired_cache_entry_refetches(mock_request):
    mock_request.return_value = MOVIE_PAYLOAD
    watch_providers.get_movie_providers(603)

    # Age every entry past its TTL.
    with watch_providers._lock:
        for key, (_, value) in list(watch_providers._cache.items()):
            watch_providers._cache[key] = (0, value)

    watch_providers.get_movie_providers(603)
    assert mock_request.call_count == 2


@patch('app.services.tmdb.try_request')
def test_tv_resolves_imdb_id_then_fetches_providers(mock_request):
    find_payload = {'tv_results': [{'id': 1396}]}
    tv_payload = {
        'results': {
            'US': {
                'link': 'https://www.themoviedb.org/tv/1396/watch?locale=US',
                'flatrate': [
                    {
                        'provider_id': 8,
                        'provider_name': 'Netflix',
                        'logo_path': '/netflix.jpg',
                        'display_priority': 0,
                    }
                ],
            }
        }
    }
    mock_request.side_effect = [find_payload, tv_payload]

    result = watch_providers.get_tv_providers('tt0903747')

    assert mock_request.call_args_list[0][0] == (
        '/find/tt0903747',
        {'external_source': 'imdb_id'},
    )
    assert mock_request.call_args_list[1][0][0] == '/tv/1396/watch/providers'
    assert [p['name'] for p in result['stream']] == ['Netflix']


@patch('app.services.tmdb.try_request')
def test_tv_without_imdb_id_skips_the_call(mock_request):
    result = watch_providers.get_tv_providers(None)

    mock_request.assert_not_called()
    assert result['stream'] == []


@patch('app.services.tmdb.try_request')
def test_tv_unresolvable_on_tmdb_returns_empty(mock_request):
    mock_request.return_value = {'tv_results': []}

    result = watch_providers.get_tv_providers('tt9999999')

    # Only the /find call happens — there's no id to fetch providers for.
    assert mock_request.call_count == 1
    assert result['stream'] == []


@patch('app.services.tmdb.try_request')
def test_provider_without_a_name_is_dropped(mock_request):
    mock_request.return_value = {
        'results': {
            'US': {
                'flatrate': [
                    {'provider_id': 1, 'provider_name': '  ', 'logo_path': '/a.jpg'},
                    {'provider_id': 2, 'provider_name': 'Hulu', 'logo_path': None},
                ]
            }
        }
    }

    result = watch_providers.get_movie_providers(603)

    assert [p['name'] for p in result['stream']] == ['Hulu']
    # A provider TMDB has no logo for still renders by name.
    assert result['stream'][0]['logo_url'] is None
