"""Tests for shared catalog search input and upstream HTTP policy."""

# pylint: disable=missing-function-docstring

import asyncio
import threading
from unittest.mock import MagicMock

import requests
from fastapi import HTTPException

from app.config import get_settings
from app.services.search_policy import (
    MIN_QUERY_LENGTH_BY_DOMAIN,
    is_bad_query_error,
    min_query_length,
    normalized_search_query,
    run_search_provider,
)


def _http_error(status_code: int) -> requests.HTTPError:
    return requests.HTTPError(response=MagicMock(status_code=status_code))


def test_query_policy_normalizes_and_trims():
    assert normalized_search_query('  matrix  ', 'Movie') == 'matrix'


def test_query_floor_follows_each_provider_not_the_strictest():
    # Probed 2026-08-20 (api#398): TMDB, TVMaze and IGDB all serve
    # one-character queries; Open Library rejects anything under three.
    # Asserting the domains differ is the point, so a future change that
    # flattens them back to one shared floor fails here.
    assert normalized_search_query('Go', 'Movie') == 'Go'
    assert normalized_search_query('Go', 'TV') == 'Go'
    assert normalized_search_query('Go', 'Game') == 'Go'
    assert normalized_search_query('Go', 'Book') is None

    searchable = {d for d in MIN_QUERY_LENGTH_BY_DOMAIN if min_query_length(d) <= 2}
    assert searchable == {'Movie', 'TV', 'Game'}


def test_unknown_domain_fails_closed_to_the_conservative_floor():
    assert normalized_search_query('Go', 'Podcast') is None


def test_bad_query_policy_keeps_auth_rate_and_server_failures_loud():
    assert is_bad_query_error(_http_error(422))
    assert not is_bad_query_error(_http_error(401))
    assert not is_bad_query_error(_http_error(403))
    assert not is_bad_query_error(_http_error(429))
    assert not is_bad_query_error(_http_error(500))


def test_provider_future_can_be_abandoned_at_request_deadline():
    release = threading.Event()

    async def run():
        await run_search_provider(release.wait)

    settings = get_settings()
    original_timeout = settings.search_handler_timeout_seconds
    settings.search_handler_timeout_seconds = 0.01
    try:
        try:
            asyncio.run(run())
        except HTTPException as exc:
            assert exc.status_code == 504
        else:
            raise AssertionError('provider future did not honor cancellation')
    finally:
        settings.search_handler_timeout_seconds = original_timeout
        release.set()
