"""
Movie search proxy.

Wraps the TMDB API so the web and MCP frontends can look up movies without
holding the API key. Results are normalized into the shape the ``/v1/movies``
create endpoint expects.

Migrated from OMDb (#163): OMDb's content is CC BY-NC, which blocked any
commercial use, and its posters hotlinked ``m.media-amazon.com``. TMDB
licenses images for application use and has no daily request cap.

Two TMDB quirks shape this module:

* **Search returns no IMDb id.** ``/search/movie`` yields TMDB ids only, so
  ``tmdb`` - not ``imdb`` - is the catalog's join key (see
  ``tracked_status._DOMAIN_CONFIG``). ``imdb`` is still stored, populated
  from the detail call, but nothing joins on it.
* **There is no IMDb rating.** ``vote_average`` is TMDB's own score and lands
  in ``rating_tmdb``; the legacy ``rating_imdb`` column keeps its imported
  values but is no longer written or displayed.
"""

import re
from datetime import datetime
from typing import List, Optional

from fastapi import HTTPException, status

from app.log.logging_config import logger
from app.services import tmdb
from app.services.search_policy import normalized_search_query

_IMDB_ID_RE = re.compile(r'^tt\d+$', re.IGNORECASE)

# OMDb returned 4 principal cast members; match that so the detail page's
# "Actors" line stays a short list rather than a full credit roll.
_CAST_LIMIT = 4
# US ratings only - the catalog is a single-region product (#163/web#26).
_CERTIFICATION_REGION = 'US'


def _year(release_date: Optional[str]) -> Optional[str]:
    """TMDB dates are 'YYYY-MM-DD'; the search shape wants the year string."""
    return (release_date or '')[:4] or None


def _to_date(value: Optional[str]) -> Optional[datetime]:
    """Parse TMDB's ISO release date; None when absent or malformed."""
    if not value:
        return None
    try:
        return datetime.strptime(value, '%Y-%m-%d')
    except ValueError:
        return None


def _normalize_hit(item: dict) -> dict:
    """Map a raw TMDB movie object to the search-hit shape callers expect."""
    return {
        'tmdb': item.get('id'),
        # Present only on the detail endpoint; search hits carry None and the
        # add flow fills it in from get_movie_detail.
        'imdb': item.get('imdb_id'),
        'title': item.get('title') or item.get('original_title'),
        'year': _year(item.get('release_date')),
        'release_date': item.get('release_date'),
        'poster_url': tmdb.image_url(item.get('poster_path')),
        'type': 'movie',
        # TMDB supplies a real popularity score; search_ranking uses it as the
        # tiebreaker that OMDb could never provide.
        'popularity': item.get('popularity'),
    }


def _search_by_imdb_id(imdb_id: str) -> List[dict]:
    """
    Resolve an IMDb-id-shaped query via TMDB's ``/find`` endpoint and map the
    result into the same search-hit shape title matches produce. Returns ``[]``
    when the id doesn't resolve, mirroring title search's "not found".
    """
    try:
        payload = tmdb.request(f'/find/{imdb_id}', {'external_source': 'imdb_id'})
    except tmdb.TmdbBadQuery as exc:
        logger.info('TMDB rejected movie id query %r: %s', imdb_id, exc)
        return []
    except (tmdb.TmdbUnconfigured, tmdb.TmdbError) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail='Upstream movie search failed',
        ) from exc
    results = payload.get('movie_results') or []
    if not results:
        return []
    hit = _normalize_hit(results[0])
    # /find echoes the id we looked up, so we can fill imdb without a second call.
    hit['imdb'] = imdb_id.lower()
    return [hit]


def search_movies(query: str) -> List[dict]:
    """
    Search TMDB for movies matching ``query``.

    A query shaped like an IMDb id (``tt`` + digits) resolves directly via
    ``/find`` instead of a title search; an id that doesn't resolve returns
    ``[]`` rather than raising.

    Returns a list of normalized dicts (``tmdb``, ``imdb``, ``title``,
    ``year``, ``poster_url``, ``type``, ``popularity``). Raises 503 when the
    API key is not configured and 502 when the upstream call fails. Queries
    shorter than three characters and provider query rejections return ``[]``.
    """
    query = normalized_search_query(query, 'Movie')
    if query is None:
        return []

    if not tmdb.is_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail='Movie search is not configured (TMDB_API_KEY missing)',
        )

    if _IMDB_ID_RE.match(query):
        return _search_by_imdb_id(query)

    try:
        payload = tmdb.request(
            '/search/movie', {'query': query, 'include_adult': 'false'}
        )
    except tmdb.TmdbUnconfigured as exc:  # pragma: no cover - guarded above
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail='Movie search is not configured (TMDB_API_KEY missing)',
        ) from exc
    except tmdb.TmdbBadQuery as exc:
        logger.info('TMDB rejected movie search query %r: %s', query, exc)
        return []
    except tmdb.TmdbError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail='Upstream movie search failed',
        ) from exc

    return [_normalize_hit(item) for item in payload.get('results') or []]


# Max lengths for the bounded catalog columns (see models_sandbox.DbMovie).
_FIELD_LIMITS = {
    'title': 255,
    'director': 512,
    'genre': 255,
    'language': 40,
    'rated': 11,
    'poster_url': 500,
    'imdb': 40,
}


def apply_detail_to_movie(movie, detail: dict) -> None:
    """
    Copy TMDB detail onto a DbMovie, truncating to column limits and only
    filling empty fields (never clobber a good value with None).
    """
    for key, value in detail.items():
        if value is None:
            continue
        if key in _FIELD_LIMITS and isinstance(value, str):
            value = value[: _FIELD_LIMITS[key]]
        setattr(movie, key, value)


def _director(credits_block: dict) -> Optional[str]:
    """Comma-joined names of everyone credited as Director."""
    crew = (credits_block or {}).get('crew') or []
    names = [c.get('name') for c in crew if c.get('job') == 'Director']
    return ', '.join(n for n in names if n) or None


def _actors(credits_block: dict) -> Optional[str]:
    """Comma-joined top-billed cast, capped at ``_CAST_LIMIT``."""
    cast = (credits_block or {}).get('cast') or []
    names = [c.get('name') for c in cast[:_CAST_LIMIT]]
    return ', '.join(n for n in names if n) or None


def _certification(release_dates: dict) -> Optional[str]:
    """
    Pull the US certification (G/PG/PG-13/R) out of TMDB's release_dates
    block. TMDB lists one entry per release type; take the first non-empty
    certification for the US.
    """
    for entry in (release_dates or {}).get('results') or []:
        if entry.get('iso_3166_1') != _CERTIFICATION_REGION:
            continue
        for release in entry.get('release_dates') or []:
            certification = (release.get('certification') or '').strip()
            if certification:
                return certification
    return None


def _language(payload: dict) -> Optional[str]:
    """
    Human-readable spoken languages ('English, French'), matching the format
    OMDb used. Falls back to the two-letter original_language code.
    """
    spoken = payload.get('spoken_languages') or []
    names = [lang.get('english_name') or lang.get('name') for lang in spoken]
    joined = ', '.join(n for n in names if n)
    return joined or payload.get('original_language') or None


def _genre(payload: dict) -> Optional[str]:
    genres = payload.get('genres') or []
    return ', '.join(g.get('name') for g in genres if g.get('name')) or None


def get_movie_detail(tmdb_id) -> Optional[dict]:
    """
    Fetch full detail for a movie by TMDB id and map it to the fields the
    catalog stores. Returns None when unavailable/unconfigured so callers can
    skip enrichment gracefully.

    ``credits`` and ``release_dates`` come back in the same round trip via
    append_to_response - director, cast and the US rating would each otherwise
    need their own call.
    """
    if not tmdb_id:
        return None
    payload = tmdb.try_request(
        f'/movie/{tmdb_id}',
        {'append_to_response': 'credits,release_dates'},
    )
    if payload is None:
        return None

    release_date = _to_date(payload.get('release_date'))
    year = release_date.year if release_date else None
    return {
        'title': payload.get('title') or payload.get('original_title'),
        'tmdb': payload.get('id'),
        'imdb': payload.get('imdb_id'),
        'year': year,
        'release_date': release_date,
        'runtime': payload.get('runtime') or None,
        'rated': _certification(payload.get('release_dates')),
        'genre': _genre(payload),
        'director': _director(payload.get('credits')),
        'actors': _actors(payload.get('credits')),
        'plot': payload.get('overview') or None,
        'language': _language(payload),
        'rating_tmdb': payload.get('vote_average') or None,
        'poster_url': tmdb.image_url(payload.get('poster_path')),
    }


def resolve_tmdb_id(imdb_id: str) -> Optional[int]:
    """
    Map an IMDb id to a TMDB id via ``/find``. Used by the backfill to key
    existing catalog rows (which only have imdb) onto TMDB. Returns None when
    TMDB has no match.
    """
    imdb_id = (imdb_id or '').strip()
    if not imdb_id:
        return None
    payload = tmdb.try_request(f'/find/{imdb_id}', {'external_source': 'imdb_id'})
    if payload is None:
        return None
    results = payload.get('movie_results') or []
    if not results:
        logger.info('TMDB has no movie for imdb id %s', imdb_id)
        return None
    return results[0].get('id')
