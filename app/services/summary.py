"""
The home summary: everything the landing page needs, in bounded queries.

The dashboard used to be assembled client-side from ``/v1/users/me/{movies,
tv-shows,books,games}`` — four unpaginated collections (~1,400 movie rows
alone) fetched, validated and shipped so the page could render eight counts
and twenty titles. This module answers the same question with two small
indexed queries per shelf, so page cost stops scaling with library size.
"""

from typing import List, Optional

from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.db.models import DbUser
from app.services.shelves import SHELVES, Shelf
from app.services.visibility import is_public

# Ranked entries returned per shelf. Five is the product ("your Top 5"), not
# an arbitrary page size — callers may ask for fewer but not more, so this
# endpoint can never become another unbounded collection.
TOP_N = 5


def _counts(db: Session, shelf: Shelf, user_pk: int):
    """Ranked and queued totals for one shelf, in a single aggregate query."""
    tracker = shelf.tracker_model
    # count() ignores NULLs, so a CASE with no ELSE counts only the matches.
    return (
        db.query(
            func.count(  # pylint: disable=not-callable
                case((tracker.on_rankings.is_(True), 1))
            ).label('ranked'),
            func.count(  # pylint: disable=not-callable
                case((tracker.on_watchlist.is_(True), 1))
            ).label('queued'),
        )
        .filter(tracker.user_id == user_pk)
        .one()
    )


def _top(db: Session, shelf: Shelf, user_pk: int, limit: int) -> List[dict]:
    """The best-ranked entries for one shelf, best first."""
    tracker, catalog = shelf.tracker_model, shelf.catalog_model
    rows = (
        db.query(tracker.rank, catalog)
        .join(catalog, getattr(tracker, shelf.join_col) == catalog.pk)
        .filter(
            tracker.user_id == user_pk,
            tracker.on_rankings.is_(True),
            tracker.rank.isnot(None),
        )
        .order_by(tracker.rank)
        .limit(limit)
        .all()
    )
    return [
        {
            'rank': rank,
            'id': item.id,
            'title': item.title,
            'year': item.year,
            'poster_url': item.poster_url,
        }
        for rank, item in rows
    ]


def build_summary(db: Session, user: DbUser, top_n: int = TOP_N) -> dict:
    """
    Assemble the home summary for ``user``.

    ``public`` per shelf and the account handle ride along so the share card
    can print a profile URL that actually resolves — the card used to derive a
    handle from the signed-in email, which is not the profile handle.
    """
    limit = max(1, min(top_n, TOP_N))
    shelves = []
    for shelf in SHELVES:
        counts = _counts(db, shelf, user.pk)
        shelves.append(
            {
                'category': shelf.category,
                'label': shelf.label,
                'ranked_count': counts.ranked,
                'queued_count': counts.queued,
                # Boolean on purpose: the share card only cares whether an
                # *anonymous* visitor would see this shelf, which is still a
                # question about ``public`` alone now that the profile itself
                # is viewer-aware (#277). A friends-only shelf is false here
                # and still reaches friends on the profile.
                'public': is_public(getattr(user, shelf.visibility_tier)),
                'top': _top(db, shelf, user.pk, limit),
            }
        )

    return {
        'handle': user.handle,
        'display_name': user.display_name,
        # A profile only resolves once a handle exists, the profile tier is
        # public, AND a shelf is opted in (see router_visibility.
        # public_profile, which 404s otherwise). The web reads this instead
        # of re-deriving the rule.
        'profile_public': bool(user.handle)
        and is_public(user.visibility_profile)
        and any(s['public'] for s in shelves),
        'shelves': shelves,
        'total_ranked': sum(s['ranked_count'] for s in shelves),
    }


def profile_path(handle: Optional[str]) -> Optional[str]:
    """Canonical public-profile path for a handle (see DbUser.handle)."""
    return f'/u/{handle}' if handle else None
