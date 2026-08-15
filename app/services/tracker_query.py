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


def list_params(  # pylint: disable=too-many-arguments,too-many-positional-arguments
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
    sort: Optional[str] = Query(
        None,
        pattern=r'^-?(rank|title|completed_at)$',
        description=(
            'Order by rank, title, or completed_at; prefix with - for descending'
        ),
    ),
    search: Optional[str] = Query(
        None, description='Case-insensitive substring match against the title'
    ),
    include_total: bool = Query(
        False,
        description=(
            'Return an {items, total, limit, offset} envelope instead of the '
            'legacy array response'
        ),
    ),
) -> dict:
    """FastAPI dependency supplying the shared tracker list query params."""
    return {
        'on_rankings': on_rankings,
        'on_watchlist': on_watchlist,
        'limit': limit,
        'offset': offset,
        'sort': sort,
        'search': search,
        'include_total': include_total,
    }


def apply_list_params(query, tracker, catalog, params: dict):
    """Apply the shared filters and ordering to a tracker query."""
    sort = params['sort']
    uses_catalog = params['search'] is not None or (
        sort is not None and sort.lstrip('-') == 'title'
    )
    if uses_catalog:
        query = query.join(catalog)
    if params['on_rankings'] is not None:
        query = query.filter(tracker.on_rankings.is_(params['on_rankings']))
    if params['on_watchlist'] is not None:
        query = query.filter(tracker.on_watchlist.is_(params['on_watchlist']))
    if params['search'] is not None:
        search = params['search']
        query = query.filter(catalog.title.ilike(f'%{search}%'))
    if sort is None:
        return query

    descending = sort.startswith('-')
    column = {
        'rank': tracker.rank,
        'title': catalog.title,
        'completed_at': tracker.completed_at,
    }[sort.lstrip('-')]
    order = column.desc() if descending else column.asc()
    # The leading predicate puts nulls at the end for both ascending and
    # descending sorts. The primary key makes pagination stable for ties.
    return query.order_by(column.is_(None), order, tracker.pk)


def list_tracker_items(query, tracker, catalog, params: dict):
    """Return the requested page and, when requested, its pre-page total."""
    query = apply_list_params(query, tracker, catalog, params)
    total = query.order_by(None).count() if params['include_total'] else None
    rows = query.offset(params['offset']).limit(params['limit']).all()
    return rows, total


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


def tracker_list_response(rows, total, params: dict, label: str):
    """Preserve legacy arrays until callers opt into the paginated envelope."""
    if not params['include_total']:
        return guard_truncation(rows, params, label)
    return {
        'items': rows,
        'total': total,
        'limit': params['limit'],
        'offset': params['offset'],
    }
