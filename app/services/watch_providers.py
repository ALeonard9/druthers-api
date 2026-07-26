"""
Streaming availability ("where can I watch this") from TMDB (web#26).

TMDB's ``/watch/providers`` endpoints resell JustWatch's availability data, so
one provider covers both movie metadata and streaming availability — which is
why this landed on top of the OMDb->TMDB migration (#163) rather than as a
separate JustWatch integration.

Two things shape this module:

* **TV is not on TMDB.** The catalog's shows come from TVMaze and carry no
  TMDB id, only an IMDb id from TVMaze's ``externals`` block. So a show has to
  be resolved through TMDB's ``/find`` endpoint first; movies already store
  the ``tmdb`` id the lookup needs.
* **Availability is live data, not catalog data.** Nothing is persisted —
  where a title streams changes without warning, and a stale row is worse
  than no row. The TTL cache below keeps detail views off TMDB's rate limit
  without pretending we own the data.

TMDB requires that any surface using this data attribute JustWatch as the
source; :data:`ATTRIBUTION` is served alongside every response so the frontends
can't render the logos without it.
"""

import re
import threading
import time
from typing import List, Optional

from app.log.logging_config import logger
from app.services import tmdb

# TMDB asks for JustWatch to be credited wherever this data is shown.
ATTRIBUTION = 'JustWatch'

DEFAULT_REGION = 'US'
_REGION_RE = re.compile(r'^[A-Za-z]{2}$')

# JustWatch refreshes availability roughly daily; six hours keeps a detail page
# off the wire without letting a removed title linger for a day.
_CACHE_TTL_SECONDS = 6 * 60 * 60

# TMDB's per-region keys, mapped to the buckets the UI groups by. ``ads`` is
# folded into ``free`` because from a viewer's side both mean "no extra
# payment" — Tubi and Pluto are ad-supported and belong next to the free tier,
# not in their own row.
_BUCKETS = {
    'stream': ('flatrate',),
    'free': ('free', 'ads'),
    'rent': ('rent',),
    'buy': ('buy',),
}

_lock = threading.Lock()
_cache: dict = {}


def reset_cache() -> None:
    """Drop every cached lookup (tests, and anything that needs a cold read)."""
    with _lock:
        _cache.clear()


def _cached(key, loader):
    """
    Memoize ``loader()`` under ``key`` for :data:`_CACHE_TTL_SECONDS`.

    Failures are cached as ``None`` too: a title TMDB has never heard of would
    otherwise re-ask on every page view.
    """
    now = time.monotonic()
    with _lock:
        entry = _cache.get(key)
        if entry is not None and entry[0] > now:
            return entry[1]

    value = loader()

    with _lock:
        _cache[key] = (now + _CACHE_TTL_SECONDS, value)
    return value


def normalize_region(region: Optional[str]) -> str:
    """
    Coerce a caller-supplied region to the ISO-3166-1 alpha-2 code TMDB keys
    its results by. Anything unrecognizable falls back to
    :data:`DEFAULT_REGION` rather than 400ing — a bad region should degrade to
    "US availability", not break the detail page.
    """
    region = (region or '').strip()
    if not _REGION_RE.match(region):
        return DEFAULT_REGION
    return region.upper()


def _provider(entry: dict) -> Optional[dict]:
    """Map one TMDB provider object to the shape the API returns."""
    name = (entry.get('provider_name') or '').strip()
    if not name:
        return None
    return {
        'provider_id': entry.get('provider_id'),
        'name': name,
        'logo_url': tmdb.image_url(entry.get('logo_path'), tmdb.LOGO_SIZE),
    }


def _bucket(region_block: dict, keys) -> List[dict]:
    """
    Collect the providers under ``keys``, ordered by TMDB's display_priority
    and de-duplicated by provider — a service listed under both ``free`` and
    ``ads`` must not render twice.
    """
    entries = []
    for key in keys:
        entries.extend(region_block.get(key) or [])
    entries.sort(key=lambda e: e.get('display_priority') or 0)

    providers = []
    seen = set()
    for entry in entries:
        provider = _provider(entry)
        if provider is None:
            continue
        marker = provider['provider_id'] or provider['name']
        if marker in seen:
            continue
        seen.add(marker)
        providers.append(provider)
    return providers


def _shape(payload: Optional[dict], region: str) -> dict:
    """
    Reduce a raw ``/watch/providers`` payload to one region's buckets.

    A title TMDB doesn't carry, or one with no availability in ``region``,
    yields the same empty-but-valid response — "we looked and found nothing"
    and "we couldn't look" are the same story for the viewer, and the frontend
    renders neither.
    """
    results = (payload or {}).get('results') or {}
    region_block = results.get(region) or {}
    return {
        'region': region,
        'link': region_block.get('link'),
        'attribution': ATTRIBUTION,
        **{name: _bucket(region_block, keys) for name, keys in _BUCKETS.items()},
    }


def get_movie_providers(tmdb_id, region: str = DEFAULT_REGION) -> dict:
    """
    Streaming availability for a movie by TMDB id.

    Always returns a valid (possibly empty) response: a movie the backfill
    couldn't key onto TMDB has no id to look up, and an unreachable TMDB
    should leave the detail page intact.
    """
    region = normalize_region(region)
    if not tmdb_id:
        return _shape(None, region)

    payload = _cached(
        ('movie', tmdb_id, region),
        lambda: tmdb.try_request(f'/movie/{tmdb_id}/watch/providers'),
    )
    return _shape(payload, region)


def _resolve_tv_tmdb_id(imdb_id: str) -> Optional[int]:
    """
    Map a show's IMDb id (stored by the TVMaze enrichment) to TMDB's tv id.
    Returns None when TMDB has no matching show.
    """
    payload = tmdb.try_request(f'/find/{imdb_id}', {'external_source': 'imdb_id'})
    if payload is None:
        return None
    results = payload.get('tv_results') or []
    if not results:
        logger.info('TMDB has no tv show for imdb id %s', imdb_id)
        return None
    return results[0].get('id')


def get_tv_providers(imdb_id: Optional[str], region: str = DEFAULT_REGION) -> dict:
    """
    Streaming availability for a TV show, keyed by the IMDb id the catalog
    stores (see module docstring — shows have no TMDB id of their own).
    """
    region = normalize_region(region)
    imdb_id = (imdb_id or '').strip()
    if not imdb_id:
        return _shape(None, region)

    tmdb_id = _cached(('tv-id', imdb_id), lambda: _resolve_tv_tmdb_id(imdb_id))
    if not tmdb_id:
        return _shape(None, region)

    payload = _cached(
        ('tv', tmdb_id, region),
        lambda: tmdb.try_request(f'/tv/{tmdb_id}/watch/providers'),
    )
    return _shape(payload, region)
