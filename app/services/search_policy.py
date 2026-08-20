"""Shared input and upstream-response policy for catalog searches."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import Optional

import requests
from fastapi import HTTPException, status

from app.config import get_settings
from app.log.logging_config import logger

# Each provider's own floor, probed directly on 2026-08-20 (api#398). These are
# observed provider limits, not a product preference, so changing one should
# follow a re-probe rather than a judgement call. TMDB, TVMaze and IGDB all
# serve one-character queries; Open Library answers 422 for anything under
# three characters, sometimes as a dropped TLS connection rather than a clean
# status. Flooring all four to Open Library's limit silently lost every
# two-letter title (Go, Up, It, Us) in the other three domains.
MIN_QUERY_LENGTH_BY_DOMAIN = {
    'Movie': 1,
    'TV': 1,
    'Game': 1,
    'Book': 3,
}
# For a domain not listed above, so a newly added domain fails closed rather
# than sending a provider prefixes it may reject.
DEFAULT_MIN_QUERY_LENGTH = 3
# Whether a spelling correction is worth a round trip is a different question
# from what a provider will accept. A one or two character string has no
# meaningful correction, so this stays at three even where the provider floor
# is one.
MIN_CORRECTION_QUERY_LENGTH = 3
_OPERATOR_HTTP_STATUSES = frozenset({401, 403, 429})
_executor = ThreadPoolExecutor(
    max_workers=get_settings().sync_thread_limit,
    thread_name_prefix='catalog-search',
)


async def run_search_provider(function, *args, **kwargs):
    """Run bounded provider work and abandon its future at the request deadline."""
    future = _executor.submit(partial(function, *args, **kwargs))
    try:
        return await asyncio.wait_for(
            asyncio.wrap_future(future),
            timeout=get_settings().search_handler_timeout_seconds,
        )
    except TimeoutError as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail='Search timed out, please try again',
        ) from exc


def search_with_correction(provider, corrector, query: str):
    """Run one provider and its optional correction retry as one bounded job."""
    results = provider(query)
    if not results and len(query.strip()) >= MIN_CORRECTION_QUERY_LENGTH:
        corrected = corrector(query)
        if corrected:
            results = provider(corrected)
    return results


def min_query_length(domain: str) -> int:
    """The shortest query this domain's provider will accept."""
    return MIN_QUERY_LENGTH_BY_DOMAIN.get(domain, DEFAULT_MIN_QUERY_LENGTH)


def normalized_search_query(query: str, domain: str) -> Optional[str]:
    """Normalize a query, returning ``None`` for an expected short prefix."""
    normalized = (query or '').strip()
    minimum = min_query_length(domain)
    if len(normalized) < minimum:
        logger.debug(
            '%s search skipped query shorter than %d characters',
            domain,
            minimum,
        )
        return None
    return normalized


def is_bad_query_error(exc: requests.HTTPError) -> bool:
    """Whether an HTTP error represents caller input rather than availability."""
    response = exc.response
    return bool(
        response is not None
        and 400 <= response.status_code < 500
        and response.status_code not in _OPERATOR_HTTP_STATUSES
    )
