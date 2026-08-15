"""
TV show search proxy.

Wraps the TVMaze API (https://www.tvmaze.com/api - free, no key) so the web
and MCP frontends can look up shows by title without talking to TVMaze
directly. Results are normalized into the shape the ``/v1/tv-shows`` create
endpoint expects. TVMaze is also the enrichment source: show detail (status,
premiered, genres, network, rating, summary, poster, imdb id via externals)
and the full episode list.
"""

import re
from datetime import datetime
from typing import List, Optional

import requests
from fastapi import HTTPException, status

from app.log.logging_config import logger

TVMAZE_URL = 'https://api.tvmaze.com'
REQUEST_TIMEOUT = 10
# TVMaze asks clients to send an identifying User-Agent so it can contact the
# operator if an application's traffic causes problems.
TVMAZE_HEADERS = {'User-Agent': 'druthers.io (Admin@druthers.io)'}


def _strip_html(value: Optional[str]) -> Optional[str]:
    """TVMaze summaries are HTML fragments; store plain text."""
    if not value:
        return None
    text = re.sub(r'<[^>]+>', '', value).strip()
    return text or None


def _to_date(value: Optional[str]) -> Optional[datetime]:
    """Parse TVMaze's ISO dates ('2013-06-24'); None when absent/invalid."""
    if not value:
        return None
    try:
        return datetime.strptime(value, '%Y-%m-%d')
    except ValueError:
        return None


def _poster(show: dict) -> Optional[str]:
    image = show.get('image') or {}
    return image.get('original') or image.get('medium')


def _network_name(show: dict) -> Optional[str]:
    network = show.get('network') or show.get('webChannel') or {}
    return network.get('name')


def _normalize_show(show: dict) -> dict:
    """Map a raw TVMaze show object to the shape callers expect."""
    premiered = show.get('premiered') or ''
    return {
        'tvmaze': show.get('id'),
        'imdb': (show.get('externals') or {}).get('imdb'),
        'title': show.get('name'),
        'year': premiered[:4] or None,
        'status': show.get('status'),
        'network': _network_name(show),
        'poster_url': _poster(show),
    }


# Bare-digit query -> IMDb-style ``tt1234567`` is unambiguous, but a run of
# plain digits is not: it could be a TheTVDB id or a numeric show title
# ("1923", "24", "9-1-1" minus the dashes). TheTVDB ids for anything catalogued
# in roughly the last decade run 5+ digits, so we only treat a 5+ digit query
# as an id lookup; shorter numeric queries fall through to a normal title
# search. This is a heuristic, not a guarantee -- see #210.
_IMDB_ID_RE = re.compile(r'tt\d+', re.IGNORECASE)
_MIN_THETVDB_ID_DIGITS = 5


def _lookup_show(params: dict) -> List[dict]:
    """
    Resolve a single show via TVMaze's ``/lookup/shows`` endpoint (exact
    match by external id). Returns ``[]`` when TVMaze has no match (404)
    rather than treating that as an error; raises 502 on other upstream
    failures.
    """
    try:
        response = requests.get(
            f'{TVMAZE_URL}/lookup/shows',
            params=params,
            timeout=REQUEST_TIMEOUT,
            headers=TVMAZE_HEADERS,
        )
        if response.status_code == 404:
            return []
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.error('TVMaze lookup failed for %r: %s', params, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail='Upstream TV search failed',
        ) from exc

    if not payload:
        return []
    return [_normalize_show(payload)]


def search_tv_shows(query: str) -> List[dict]:
    """
    Search TVMaze for shows matching ``query``.

    A query shaped like an IMDb id (``tt`` + digits) or a run of 5+ digits
    (treated as a TheTVDB id) resolves directly via TVMaze's
    ``/lookup/shows`` endpoint instead of a title search; an id-shaped query
    that doesn't resolve returns ``[]`` rather than raising. Ordinary title
    queries are unaffected.

    Returns a list of normalized dicts (``tvmaze``, ``imdb``, ``title``,
    ``year``, ``status``, ``network``, ``poster_url``). Raises 400 on an
    empty query and 502 when the upstream call fails.
    """
    query = (query or '').strip()
    if not query:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Search query must not be empty',
        )

    if _IMDB_ID_RE.fullmatch(query):
        return _lookup_show({'imdb': query.lower()})

    if query.isdigit() and len(query) >= _MIN_THETVDB_ID_DIGITS:
        return _lookup_show({'thetvdb': query})

    try:
        response = requests.get(
            f'{TVMAZE_URL}/search/shows',
            params={'q': query},
            timeout=REQUEST_TIMEOUT,
            headers=TVMAZE_HEADERS,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.error('TVMaze search failed for %r: %s', query, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail='Upstream TV search failed',
        ) from exc

    results = []
    for item in payload or []:
        show = item.get('show') or {}
        results.append(_normalize_show(show))
    return results


# Max lengths for the bounded catalog columns (see models_sandbox.DbTVShow).
_FIELD_LIMITS = {
    'title': 254,
    'imdb': 254,
    'status': 254,
    'poster_url': 254,
    'genre': 255,
    'network': 255,
    'language': 40,
}


def apply_detail_to_show(show, detail: dict) -> None:
    """
    Copy TVMaze detail onto a DbTVShow, truncating to column limits and
    skipping None values (never clobber a good value with None).
    """
    for key, value in detail.items():
        if value is None:
            continue
        if key in _FIELD_LIMITS and isinstance(value, str):
            value = value[: _FIELD_LIMITS[key]]
        setattr(show, key, value)


def get_tv_show_detail(tvmaze_id: Optional[int]) -> Optional[dict]:
    """
    Fetch full detail for a show by TVMaze id and map it to the fields the
    catalog stores. Returns None when unavailable so callers can skip
    enrichment gracefully.
    """
    if not tvmaze_id:
        return None
    try:
        response = requests.get(
            f'{TVMAZE_URL}/shows/{int(tvmaze_id)}',
            timeout=REQUEST_TIMEOUT,
            headers=TVMAZE_HEADERS,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning('TVMaze detail failed for %s: %s', tvmaze_id, exc)
        return None

    premiered = _to_date(payload.get('premiered'))
    genres = payload.get('genres') or []
    return {
        'title': payload.get('name'),
        'tvmaze': payload.get('id'),
        'imdb': (payload.get('externals') or {}).get('imdb'),
        'status': payload.get('status'),
        'premiered': premiered,
        'year': premiered.year if premiered else None,
        'genre': ', '.join(genres) if genres else None,
        'network': _network_name(payload),
        'runtime': payload.get('averageRuntime') or payload.get('runtime'),
        'language': payload.get('language'),
        'rating': (payload.get('rating') or {}).get('average'),
        'summary': _strip_html(payload.get('summary')),
        'poster_url': _poster(payload),
    }


def get_show_episodes(tvmaze_id: Optional[int]) -> List[dict]:
    """
    Fetch the full episode list for a show and normalize each episode to the
    catalog's fields (``tvmaze``, ``title``, ``season``, ``season_number``,
    ``airdate``). Returns [] when unavailable.
    """
    if not tvmaze_id:
        return []
    try:
        response = requests.get(
            f'{TVMAZE_URL}/shows/{int(tvmaze_id)}/episodes',
            timeout=REQUEST_TIMEOUT,
            headers=TVMAZE_HEADERS,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning('TVMaze episodes failed for %s: %s', tvmaze_id, exc)
        return []

    episodes = []
    for item in payload or []:
        episodes.append(
            {
                'tvmaze': item.get('id'),
                'title': item.get('name') or 'Untitled',
                'season': item.get('season'),
                'season_number': item.get('number'),
                'airdate': _to_date(item.get('airdate')),
            }
        )
    return episodes


def sync_episodes(db, show) -> int:
    """
    Upsert the TVMaze episode list for ``show`` into the catalog. Returns the
    number of episodes created; existing episodes get their title/season/
    airdate refreshed.

    Episodes are matched on their TVMaze id first, then on their slot in the
    show - ``(season, season_number)``. The slot fallback matters because
    TVMaze reassigns episode ids (#169): keying on the id alone, a reassigned
    episode missed the lookup and was inserted as a *second* row for the same
    slot. Watch history lives on the original row's ``episode_id``, so the
    episode silently reverted to unwatched. Matching the slot updates the id
    on the row that already exists, and the history stays attached.
    """
    # Imported locally to avoid a service->models import at module load in
    # callers that only need search.
    from app.db.models_sandbox import (  # pylint: disable=import-outside-toplevel
        DbTVEpisode,
    )

    episodes = get_show_episodes(show.tvmaze)
    if not episodes:
        return 0

    rows = db.query(DbTVEpisode).filter(DbTVEpisode.tv_show_id == show.pk).all()
    by_tvmaze = {ep.tvmaze: ep for ep in rows if ep.tvmaze}
    # Ambiguous slots (an existing duplicate pair) are left out: reconciling
    # those is audit_watch_gaps' job, and guessing here could pick the orphan.
    by_slot = {}
    for ep in rows:
        slot = (ep.season, ep.season_number)
        if slot in by_slot:
            by_slot[slot] = None
        else:
            by_slot[slot] = ep

    created = 0
    for data in episodes:
        current = by_tvmaze.get(data['tvmaze'])
        if current is None:
            current = by_slot.get((data.get('season'), data.get('season_number')))
        if current is not None:
            for key, value in data.items():
                if value is not None:
                    setattr(current, key, value)
        else:
            db.add(DbTVEpisode(tv_show_id=show.pk, **data))
            created += 1
    return created
