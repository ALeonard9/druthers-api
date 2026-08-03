"""
Video game search proxy.

Wraps the IGDB API (https://api.igdb.com — authenticated with Twitch OAuth
client credentials, mirroring the legacy orion games page) so the web and
MCP frontends can look up games without holding the credentials. Results are
normalized into the shape the ``/v1/games`` create endpoint expects. IGDB is
also the enrichment source, keyed on the catalog's ``igdb`` id: release
year, genres, platforms, summary, rating (0–100), and cover art.

Raises 503 when ``TWITCH_CLIENT_ID``/``TWITCH_CLIENT_SECRET`` are not
configured (same graceful degradation as the TMDB movie search).
"""

import time
from datetime import datetime, timezone
from typing import List, Optional, Tuple

import requests
from fastapi import HTTPException, status

from app.config import get_settings
from app.log.logging_config import logger

TWITCH_OAUTH_URL = 'https://id.twitch.tv/oauth2/token'
IGDB_URL = 'https://api.igdb.com/v4'
COVER_URL = 'https://images.igdb.com/igdb/image/upload/t_cover_big_2x'
REQUEST_TIMEOUT = 10

_DETAIL_FIELDS = (
    'name,slug,first_release_date,total_rating,genres.name,'
    'platforms.abbreviation,summary,cover.image_id,updated_at'
)

# (token, expires_at_epoch) — Twitch app tokens last ~60 days; refresh early.
_token_cache: Tuple[Optional[str], float] = (None, 0.0)


def _credentials() -> Tuple[str, str]:
    settings = get_settings()
    if not (settings.twitch_client_id and settings.twitch_client_secret):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail='Game search is not configured '
            '(TWITCH_CLIENT_ID/TWITCH_CLIENT_SECRET missing)',
        )
    return settings.twitch_client_id, settings.twitch_client_secret


def _access_token() -> str:
    """Return a cached Twitch app token, refreshing when near expiry."""
    global _token_cache  # pylint: disable=global-statement
    token, expires_at = _token_cache
    if token and time.time() < expires_at - 300:
        return token

    client_id, client_secret = _credentials()
    try:
        response = requests.post(
            TWITCH_OAUTH_URL,
            params={
                'client_id': client_id,
                'client_secret': client_secret,
                'grant_type': 'client_credentials',
            },
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.error('Twitch OAuth token request failed: %s', exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail='Upstream game auth failed',
        ) from exc

    token = payload['access_token']
    _token_cache = (token, time.time() + payload.get('expires_in', 3600))
    return token


def _igdb_query(endpoint: str, body: str) -> list:
    """
    POST an APIcalypse query to IGDB and return the JSON list.

    On a 401 (token revoked/rotated before its cached expiry) the token
    cache is evicted and the request retried once with a fresh token.
    """
    global _token_cache  # pylint: disable=global-statement
    client_id, _ = _credentials()
    for attempt in (1, 2):
        headers = {
            'Client-ID': client_id,
            'Authorization': f'Bearer {_access_token()}',
        }
        response = requests.post(
            f'{IGDB_URL}/{endpoint}',
            data=body,
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        )
        if response.status_code == 401 and attempt == 1:
            _token_cache = (None, 0.0)
            continue
        response.raise_for_status()
        return response.json()
    return []  # unreachable; keeps the type checker honest


def _cover(game: dict) -> Optional[str]:
    image_id = (game.get('cover') or {}).get('image_id')
    return f'{COVER_URL}/{image_id}.jpg' if image_id else None


def _release(game: dict) -> Optional[datetime]:
    ts = game.get('first_release_date')
    if not ts:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).replace(tzinfo=None)


def _names(game: dict, key: str, name_key: str = 'name') -> Optional[str]:
    items = game.get(key) or []
    names = [i.get(name_key) for i in items if i.get(name_key)]
    return ', '.join(names) if names else None


_SEARCH_FIELDS = 'name,slug,first_release_date,platforms.abbreviation,cover.image_id'


def _search_hit(game: dict) -> dict:
    """Normalize a raw IGDB game record into the search-hit shape."""
    release = _release(game)
    return {
        'igdb': game.get('id'),
        'title': game.get('name'),
        'slug': game.get('slug'),
        'year': str(release.year) if release else None,
        'platforms': _names(game, 'platforms', 'abbreviation'),
        'poster_url': _cover(game),
    }


def _search_games_by_id(igdb_id: int) -> List[dict]:
    """
    Look up a single game by IGDB id and return it in the search-hit shape.

    Returns an empty list when the id doesn't resolve to a game, matching
    the empty-result behavior of a title search with no matches.
    """
    try:
        payload = _igdb_query(
            'games',
            f'fields {_SEARCH_FIELDS}; where id = {igdb_id};',
        )
    except (requests.RequestException, ValueError) as exc:
        logger.error('IGDB id lookup failed for %s: %s', igdb_id, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail='Upstream game search failed',
        ) from exc

    if not payload:
        return []
    return [_search_hit(payload[0])]


def search_games(query: str) -> List[dict]:
    """
    Search IGDB for games matching ``query``.

    A bare-numeric query is treated as an IGDB id and resolved directly via
    ``where id = <id>`` instead of a fuzzy title search. Returns a list of
    normalized dicts (``igdb``, ``title``, ``year``, ``platforms``,
    ``poster_url``). Raises 400 on an empty query, 503 when unconfigured,
    and 502 when the upstream call fails.
    """
    query = (query or '').strip()
    if not query:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Search query must not be empty',
        )

    if query.isdigit():
        return _search_games_by_id(int(query))

    # Escape backslashes first, then quotes — otherwise a trailing backslash
    # (or crafted \" sequence) breaks out of the APIcalypse string literal.
    escaped = query.replace('\\', '\\\\').replace('"', '\\"')
    try:
        payload = _igdb_query(
            'games',
            f'search "{escaped}"; fields name,slug,first_release_date,'
            'platforms.abbreviation,cover.image_id,total_rating_count; '
            'limit 20;',
        )
    except (requests.RequestException, ValueError) as exc:
        # HTTPExceptions from _igdb_query (503 unconfigured / 502 auth)
        # propagate untouched — they aren't caught here.
        logger.error('IGDB search failed for %r: %s', query, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail='Upstream game search failed',
        ) from exc

    results = []
    for game in payload:
        release = _release(game)
        results.append(
            {
                'igdb': game.get('id'),
                'title': game.get('name'),
                'slug': game.get('slug'),
                'year': str(release.year) if release else None,
                'platforms': _names(game, 'platforms', 'abbreviation'),
                'poster_url': _cover(game),
                # Ranking-only signal (see search_ranking.py) — not part of
                # the /v1/games create shape, dropped by the response schema.
                'popularity': game.get('total_rating_count') or 0,
            }
        )
    return results


# Max lengths for the bounded catalog columns (see models_sandbox.DbVideoGame).
_FIELD_LIMITS = {
    'title': 255,
    'slug': 255,
    'poster_url': 254,
    'genre': 255,
    'platforms': 254,
}


def apply_detail_to_game(game, detail: dict) -> None:
    """
    Copy IGDB detail onto a DbVideoGame, truncating to column limits and
    skipping None values (never clobber a good value with None).
    """
    for key, value in detail.items():
        if value is None:
            continue
        if key in _FIELD_LIMITS and isinstance(value, str):
            value = value[: _FIELD_LIMITS[key]]
        setattr(game, key, value)


def _detail_from_game(game: dict) -> dict:
    """Map one raw IGDB game record (``_DETAIL_FIELDS`` shape) to catalog fields."""
    release = _release(game)
    rating = game.get('total_rating')
    updated = game.get('updated_at')
    return {
        'title': game.get('name'),
        'igdb': game.get('id'),
        'slug': game.get('slug'),
        'release_date': release,
        'year': release.year if release else None,
        'genre': _names(game, 'genres'),
        'platforms': _names(game, 'platforms', 'abbreviation'),
        'summary': game.get('summary'),
        'rating': round(rating, 1) if rating else None,
        'poster_url': _cover(game),
        'igdb_last_update': (
            datetime.fromtimestamp(updated, tz=timezone.utc).replace(tzinfo=None)
            if updated
            else None
        ),
    }


def get_game_detail(igdb_id: Optional[int]) -> Optional[dict]:
    """
    Fetch full detail for a game by IGDB id and map it to the fields the
    catalog stores. Returns None when unavailable/unconfigured so callers
    can skip enrichment gracefully.
    """
    if not igdb_id:
        return None
    try:
        payload = _igdb_query(
            'games',
            f'fields {_DETAIL_FIELDS}; where id = {int(igdb_id)};',
        )
    except HTTPException:
        # Unconfigured (503) or upstream auth failure — skip enrichment.
        return None
    except (requests.RequestException, ValueError) as exc:
        logger.warning('IGDB detail failed for %s: %s', igdb_id, exc)
        return None
    if not payload:
        return None
    return _detail_from_game(payload[0])


def get_time_to_beat(igdb_id: Optional[int]) -> Optional[int]:
    """
    Fetch the "normally" (main story) completion time for a game by IGDB
    id, in whole hours. IGDB reports this on a separate endpoint from game
    detail, keyed by the same id, in seconds. Returns None when unavailable
    (no community data, unconfigured, or upstream failure) so callers can
    leave the column null rather than treating a miss as zero.
    """
    if not igdb_id:
        return None
    try:
        payload = _igdb_query(
            'game_time_to_beats',
            f'fields normally; where game_id = {int(igdb_id)};',
        )
    except HTTPException:
        return None
    except (requests.RequestException, ValueError) as exc:
        logger.warning('IGDB time-to-beat failed for %s: %s', igdb_id, exc)
        return None
    if not payload:
        return None
    seconds = payload[0].get('normally')
    return round(seconds / 3600) if seconds else None


def list_popular_games(limit: int = 500) -> List[dict]:
    """
    The ``limit`` highest-rated-by-volume games, in full catalog-field shape
    (one request — ``_DETAIL_FIELDS`` already carries everything
    :func:`get_game_detail` would need a second call for).

    IGDB has no free-text "popular" concept; ``total_rating_count`` — how
    many people rated it — is the closest proxy and, unlike
    ``total_rating`` alone, isn't dominated by obscure games with one 10/10
    vote. Used to build the real-catalog dev seed fixture (#228), not by any
    request-serving path.
    """
    payload = _igdb_query(
        'games',
        f'fields {_DETAIL_FIELDS}; sort total_rating_count desc; '
        f'where total_rating_count > 10; limit {int(limit)};',
    )
    return [_detail_from_game(game) for game in payload]
