"""
Shared list-shaping for the per-user tracker collections.

``/v1/users/me/{movies,tv-shows,books,games}`` returned every tracker a user
owns with no ceiling - ~1,400 rows (≈400KB) for movies alone. Callers that
only want one list ("what's ranked?") had to fetch everything and filter
client-side. These helpers give all four endpoints the same filters, the same
paging, and a real upper bound.
"""

from typing import Optional

from fastapi import HTTPException, Query, status

# Hard ceiling on any single tracker page. Set well above today's largest
# library so nothing breaks, but low enough that the endpoint can no longer
# return an unbounded result set.
MAX_PAGE = 5000


def list_params(
    on_rankings: Optional[bool] = Query(
        None, description='Only ranked entries (true) or only unranked (false)'
    ),
    on_watchlist: Optional[bool] = Query(
        None, description='Only queued entries (true) or only unqueued (false)'
    ),
    limit: int = Query(
        MAX_PAGE, ge=1, le=MAX_PAGE, description='Maximum entries to return'
    ),
    offset: int = Query(0, ge=0, description='Entries to skip'),
) -> dict:
    """FastAPI dependency supplying the shared tracker list query params."""
    return {
        'on_rankings': on_rankings,
        'on_watchlist': on_watchlist,
        'limit': limit,
        'offset': offset,
    }


def apply_list_params(query, tracker, params: dict):
    """Apply the shared filters and paging to a tracker query."""
    if params['on_rankings'] is not None:
        query = query.filter(tracker.on_rankings.is_(params['on_rankings']))
    if params['on_watchlist'] is not None:
        query = query.filter(tracker.on_watchlist.is_(params['on_watchlist']))
    return query.offset(params['offset']).limit(params['limit'])


def guard_truncation(rows, params: dict, label: str):
    """
    Refuse to silently truncate.

    A caller that asked for the default page and got exactly ``MAX_PAGE`` rows
    has outgrown the endpoint; returning a silently-clipped list would corrupt
    a rankings board. Fail loudly instead so it surfaces as an error rather
    than as missing entries.
    """
    if len(rows) == params['limit'] == MAX_PAGE:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=(
                f'{label} library exceeds {MAX_PAGE} entries - '
                'page it with limit/offset'
            ),
        )
    return rows
