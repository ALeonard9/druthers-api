"""
Build the checked-in real-catalog fixtures ``app/migration/fixtures/seed_*.json``
that ``app.migration.seed_dev`` seeds local dev from (#228).

This is an occasional maintenance tool, not part of any deploy or dev-up
path -- run it by hand when the fixture should be refreshed with newer
titles. It only reads from the providers and writes JSON files; it never
touches a database.

Usage::

    TMDB_API_KEY=... TWITCH_CLIENT_ID=... TWITCH_CLIENT_SECRET=... \\
        python -m app.migration.build_seed_fixtures
"""

import json
import random
import time
from pathlib import Path
from typing import Optional

import requests

from app.log.logging_config import logger
from app.services import tmdb
from app.services.book_search import (
    OPENLIBRARY_HEADERS,
    OPENLIBRARY_URL,
    get_book_detail,
)
from app.services.game_search import list_popular_games
from app.services.movie_search import get_movie_detail
from app.services.tv_search import (
    TVMAZE_HEADERS,
    TVMAZE_URL,
    get_show_episodes,
    get_tv_show_detail,
)

FIXTURES_DIR = Path(__file__).parent / 'fixtures'
REQUEST_TIMEOUT = 10

# Kept comfortably under the repo's 200KB pre-commit file-size cap (checked
# compact, not pretty-printed -- see _write).
MOVIE_TARGET = 270
TV_TARGET = 80
BOOK_TARGET = 70
GAME_TARGET = 60

# Long-running soaps/talk shows can carry 1000+ episodes; a fixture only
# needs enough to exercise pagination, not the full run -- and enough
# headroom under the 200KB cap for movies/books/games too.
MAX_EPISODES_PER_SHOW = 15

# Minimum TVMaze rating for a show to be worth seeding -- keeps the fixture
# to titles a person would actually recognize rather than the obscure tail.
_MIN_SHOW_RATING = 6.5

_BOOK_SUBJECTS = (
    'fiction',
    'fantasy',
    'mystery',
    'romance',
    'biography',
    'history',
    'science',
    'poetry',
    'young_adult',
    'self_help',
)


def _json_safe(value):
    """Recursively convert datetimes to ISO strings so ``json.dump`` accepts them."""
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if hasattr(value, 'isoformat'):
        return value.isoformat()
    return value


def _write(name: str, rows: list) -> None:
    path = FIXTURES_DIR / name
    # Compact, not pretty-printed: these are read by seed_dev, never by a
    # person, and indentation alone roughly doubled seed_tv_shows.json past
    # the repo's 200KB pre-commit file-size cap.
    path.write_text(json.dumps(_json_safe(rows), separators=(',', ':')) + '\n')
    print(f'wrote {len(rows)} rows -> {path}')


def build_movies() -> None:
    """Popular + top-rated TMDB movies, deduped and detail-fetched."""
    ids = []
    for endpoint in ('/movie/popular', '/movie/top_rated'):
        for page in range(1, 11):
            payload = tmdb.request(endpoint, {'page': page})
            ids.extend(r['id'] for r in payload.get('results') or [])
    ids = list(dict.fromkeys(ids))
    random.shuffle(ids)

    rows = []
    for tmdb_id in ids:
        if len(rows) >= MOVIE_TARGET:
            break
        detail = get_movie_detail(tmdb_id)
        # Skip anything TMDB didn't give a year for -- the fixture exists
        # partly so the dev Top 5 always has real years to render (#226/#227).
        if detail and detail.get('year'):
            rows.append(detail)
        time.sleep(0.25)
    _write('seed_movies.json', rows)


def build_tv_shows() -> None:
    """Well-rated, English, scripted TVMaze shows, with a capped episode list."""
    candidates = []
    page = 0
    while len(candidates) < TV_TARGET * 6:
        response = requests.get(
            f'{TVMAZE_URL}/shows',
            params={'page': page},
            headers=TVMAZE_HEADERS,
            timeout=REQUEST_TIMEOUT,
        )
        if response.status_code == 404:
            break
        response.raise_for_status()
        candidates.extend(response.json())
        page += 1
        time.sleep(0.3)

    good = [
        s
        for s in candidates
        if s.get('type') == 'Scripted'
        and s.get('language') == 'English'
        and ((s.get('rating') or {}).get('average') or 0) >= _MIN_SHOW_RATING
    ]
    random.shuffle(good)

    rows = []
    for show in good:
        if len(rows) >= TV_TARGET:
            break
        detail = get_tv_show_detail(show['id'])
        if not detail or not detail.get('year'):
            continue
        detail['episodes'] = get_show_episodes(show['id'])[:MAX_EPISODES_PER_SHOW]
        rows.append(detail)
        time.sleep(0.3)
    _write('seed_tv_shows.json', rows)


def _resolve_isbn(edition_olid: Optional[str]) -> Optional[str]:
    """An edition's ISBN-13 (falling back to ISBN-10), or None on any miss."""
    if not edition_olid:
        return None
    try:
        response = requests.get(
            f'{OPENLIBRARY_URL}/books/{edition_olid}.json',
            headers=OPENLIBRARY_HEADERS,
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning(
            'Open Library edition fetch failed for %s: %s', edition_olid, exc
        )
        return None
    isbn_13 = data.get('isbn_13') or []
    isbn_10 = data.get('isbn_10') or []
    return (isbn_13 or isbn_10 or [None])[0]


def build_books() -> None:
    """Real books drawn from a handful of popular Open Library subjects."""
    works = []
    for subject in _BOOK_SUBJECTS:
        response = requests.get(
            f'{OPENLIBRARY_URL}/subjects/{subject}.json',
            params={'limit': 40},
            headers=OPENLIBRARY_HEADERS,
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        works.extend(response.json().get('works') or [])
        time.sleep(0.3)

    seen_keys = set()
    unique_works = []
    for work in works:
        if work['key'] in seen_keys:
            continue
        seen_keys.add(work['key'])
        unique_works.append(work)
    random.shuffle(unique_works)

    rows = []
    for work in unique_works:
        if len(rows) >= BOOK_TARGET:
            break
        isbn = _resolve_isbn(work.get('cover_edition_key'))
        if not isbn:
            continue
        detail = get_book_detail(isbn)
        if detail and detail.get('year'):
            rows.append(detail)
        time.sleep(0.3)
    _write('seed_books.json', rows)


def build_games() -> None:
    """The highest-rating-count IGDB games, already in full detail shape."""
    games = list_popular_games(limit=GAME_TARGET * 3)
    random.shuffle(games)
    rows = [g for g in games if g.get('year')][:GAME_TARGET]
    _write('seed_games.json', rows)


def main() -> None:
    """Build all four fixtures."""
    FIXTURES_DIR.mkdir(exist_ok=True)
    build_movies()
    build_tv_shows()
    build_books()
    build_games()


if __name__ == '__main__':
    main()
