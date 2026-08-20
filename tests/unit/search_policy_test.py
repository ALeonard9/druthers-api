"""Tests for shared catalog search input and upstream HTTP policy."""

# pylint: disable=missing-function-docstring

import asyncio
import threading
from unittest.mock import MagicMock

import requests
from fastapi import HTTPException

from app.config import get_settings
from app.services.search_policy import (
    is_bad_query_error,
    normalized_search_query,
    run_search_provider,
)


def _http_error(status_code: int) -> requests.HTTPError:
    return requests.HTTPError(response=MagicMock(status_code=status_code))


def test_query_policy_normalizes_and_rejects_short_prefixes():
    assert normalized_search_query('  matrix  ', 'Movie') == 'matrix'
    assert normalized_search_query('Go', 'Movie') is None


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
