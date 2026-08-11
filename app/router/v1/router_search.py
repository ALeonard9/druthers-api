# pylint: disable=missing-function-docstring
"""
Global cross-domain search: one query fanned out to every provider.

Providers are independent external APIs, so they run in parallel; a provider
failing (or being unconfigured) yields an empty list for its domain rather
than failing the whole search. Domains that come back empty are retried once
with a spelling correction — some providers fuzzy-match and some don't.
"""

from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth.oauth2 import get_current_user
from app.db.database import get_db
from app.db.db_user import search_users
from app.schemas.model_schemas import OutUserSearchResponse
from app.schemas.schemas_sandbox import GlobalSearchResponse
from app.services.rate_limit import search_rate_limit
from app.services.book_search import search_books
from app.services.game_search import search_games
from app.services.movie_search import search_movies
from app.services.search_correction import correct_query
from app.services.search_ranking import rank_and_cap
from app.services.tracked_status import attach_tracked_status
from app.services.tv_search import search_tv_shows

router = APIRouter(prefix='/v1', tags=['Search'])


def _providers() -> Dict[str, Callable[[str], List[dict]]]:
    # Resolved at call time (not module load) so tests can patch the
    # module-level search functions.
    return {
        'movies': search_movies,
        'tv_shows': search_tv_shows,
        'games': search_games,
        'books': search_books,
    }


def _fan_out(q: str, only: Optional[List[str]] = None) -> Dict[str, List[dict]]:
    def run(fn: Callable[[str], List[dict]]) -> List[dict]:
        try:
            return fn(q)
        except HTTPException:
            # Unconfigured/unavailable provider: skip its domain, keep the rest.
            return []

    providers = _providers()
    if only is not None:
        providers = {name: fn for name, fn in providers.items() if name in only}
    with ThreadPoolExecutor(max_workers=max(len(providers), 1)) as pool:
        futures = {name: pool.submit(run, fn) for name, fn in providers.items()}
        return {name: future.result() for name, future in futures.items()}


@router.get(
    '/search',
    response_model=GlobalSearchResponse,
    dependencies=[Depends(search_rate_limit)],
)
def global_search(
    q: str,
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
):
    results = _fan_out(q)
    corrected = None
    # Track which query actually produced each domain's hits, so ranking
    # (below) scores against the right string.
    query_by_domain = dict.fromkeys(results, q)
    # Some providers fuzzy-match and some don't, so retry only the domains
    # that came back empty with a spell-corrected query.
    empty = [name for name, hits in results.items() if not hits]
    if empty:
        respelled = correct_query(q)
        if respelled:
            retried = _fan_out(respelled, only=empty)
            if any(retried.values()):
                corrected = respelled
                results.update(retried)
                for name in empty:
                    query_by_domain[name] = respelled
    # Cap to the top DEFAULT_DOMAIN_CAP per domain, best match first (see
    # search_ranking for the exact-match/partial-match/popularity
    # heuristic), before the tracked-status lookup so it only does DB work
    # for the results that are actually shown.
    results = {
        domain: rank_and_cap(query_by_domain[domain], hits)
        for domain, hits in results.items()
    }
    user_pk = current_user[0].pk
    for domain, hits in results.items():
        attach_tracked_status(db, user_pk, hits, domain)
    return GlobalSearchResponse(query=q, corrected=corrected, **results)


@router.get(
    '/search/users',
    response_model=OutUserSearchResponse,
    dependencies=[Depends(search_rate_limit)],
)
def search_users_route(
    q: str,
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
):
    if not q or not q.strip():
        return OutUserSearchResponse(query=q, corrected=None, users=[])

    user_pk = current_user[0].pk
    hits = search_users(db, user_pk, q.strip(), limit=20)

    return OutUserSearchResponse(query=q, corrected=None, users=hits)
