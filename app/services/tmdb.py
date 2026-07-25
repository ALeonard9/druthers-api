"""
Shared TMDB HTTP client.

One place for auth, timeouts, 429 handling and image URL construction so the
movie search/detail/watch-provider services don't each re-implement them.

Two call styles, mirroring how the catalog services already behave:

``request``
    Raises :class:`TmdbUnconfigured` / :class:`TmdbError` so a *user-facing
    search* can turn them into 503/502.
``try_request``
    Returns ``None`` on any failure so *enrichment* stays best-effort and
    never breaks an add or a detail view.

TMDB publishes no daily cap; the limit is roughly 40-50 requests/second with
20 connections per IP, enforced by a 429 response. Their docs ask callers to
"be respectful of the service" and to respond to 429 appropriately, which is
what ``_RETRY_STATUS`` handling below does.
"""

import time
from typing import Optional

import requests

from app.config import get_settings
from app.log.logging_config import logger

TMDB_URL = 'https://api.themoviedb.org/3'
IMAGE_BASE = 'https://image.tmdb.org/t/p'
REQUEST_TIMEOUT = 10

# w500 is TMDB's standard poster width; the catalog's poster_url column caps at
# 500 chars, and these URLs are ~55, so there's plenty of headroom.
POSTER_SIZE = 'w500'
# Provider logos render small (the detail page shows them inline at ~24px).
LOGO_SIZE = 'w92'

_RETRY_STATUS = (429, 502, 503, 504)
_MAX_ATTEMPTS = 3
# Fallback pause when TMDB returns 429 without a Retry-After header.
_DEFAULT_BACKOFF_SECONDS = 2.0


class TmdbUnconfigured(RuntimeError):
    """TMDB_API_KEY is not set."""


class TmdbError(RuntimeError):
    """TMDB was unreachable or returned an unusable response."""


def is_configured() -> bool:
    """True when a TMDB API key is available."""
    return bool(get_settings().tmdb_api_key)


def image_url(path: Optional[str], size: str = POSTER_SIZE) -> Optional[str]:
    """
    Build a full image URL from TMDB's relative ``poster_path``/``logo_path``.

    Returns None for a missing path so callers can store NULL rather than a
    URL that would 404.
    """
    if not path:
        return None
    return f'{IMAGE_BASE}/{size}{path}'


def _retry_after_seconds(response, attempt: int) -> float:
    """Honor TMDB's Retry-After when present, else back off exponentially."""
    header = response.headers.get('Retry-After') if response is not None else None
    if header:
        try:
            return float(header)
        except (TypeError, ValueError):
            pass
    return _DEFAULT_BACKOFF_SECONDS * attempt


def request(path: str, params: Optional[dict] = None) -> dict:
    """
    GET ``path`` (e.g. ``/movie/603``) and return the decoded JSON body.

    Retries 429/5xx up to ``_MAX_ATTEMPTS`` times, then raises. A 404 is *not*
    retried and raises :class:`TmdbError` — callers that treat "no such movie"
    as an empty result should use :func:`try_request` instead.
    """
    settings = get_settings()
    if not settings.tmdb_api_key:
        raise TmdbUnconfigured('TMDB_API_KEY missing')

    query = dict(params or {})
    query['api_key'] = settings.tmdb_api_key

    last_exc: Optional[Exception] = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        response = None
        try:
            response = requests.get(
                f'{TMDB_URL}{path}',
                params=query,
                timeout=REQUEST_TIMEOUT,
            )
            if response.status_code in _RETRY_STATUS:
                # Exhausted: report the throttling explicitly rather than
                # letting raise_for_status decide what this means.
                if attempt >= _MAX_ATTEMPTS:
                    last_exc = TmdbError(
                        f'{response.status_code} after {attempt} attempts'
                    )
                    break
                pause = _retry_after_seconds(response, attempt)
                logger.warning(
                    'TMDB %s returned %s; retrying in %.1fs (attempt %d/%d)',
                    path,
                    response.status_code,
                    pause,
                    attempt,
                    _MAX_ATTEMPTS,
                )
                time.sleep(pause)
                continue
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            # A non-retryable status (404), a decode error, or a transport
            # failure — all terminal, since retryable statuses are handled
            # above before raise_for_status runs.
            last_exc = exc
            break

    logger.error('TMDB request failed for %s: %s', path, last_exc)
    raise TmdbError(f'TMDB request failed for {path}') from last_exc


def try_request(path: str, params: Optional[dict] = None) -> Optional[dict]:
    """
    Best-effort variant of :func:`request` — returns None instead of raising,
    for enrichment paths that must degrade quietly.
    """
    try:
        return request(path, params)
    except (TmdbUnconfigured, TmdbError):
        return None
