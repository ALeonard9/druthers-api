"""Viewer-safe, four-domain comparison calculations (#281)."""

from sqlalchemy.orm import Session

from app.services.shelves import Shelf
from app.services.visibility import VisibilityTier, admits

MIN_SHARED_FOR_SCORE = 5
RESULT_LIMIT = 5
METHOD = (
    'We compare rank positions directly. Smaller differences mean closer agreement.'
)


def _rank_gap(your_rank: int, their_rank: int) -> int:
    """Return the absolute number of places between two rankings."""
    return abs(your_rank - their_rank)


def _item(catalog, **extra) -> dict:
    return {
        'id': str(catalog.id),
        'title': catalog.title,
        'year': catalog.year,
        'poster_url': catalog.poster_url,
        **extra,
    }


def compare_shelf(  # pylint: disable=too-many-locals
    db: Session,
    viewer,
    target,
    shelf: Shelf,
    ceiling: VisibilityTier,
) -> dict:
    """Compare one shelf without ever reading target data above the ceiling."""
    ranked_visible = admits(ceiling, getattr(target, shelf.visibility_tier))
    watchlist_visible = ranked_visible and admits(
        ceiling, getattr(target, shelf.watchlist_visibility_tier)
    )
    base = {
        'category': shelf.category,
        'label': shelf.label,
        'rankings_visible': ranked_visible,
        'watchlist_visible': watchlist_visible,
        'common_watchlist': [],
        'recommendations': [],
        'biggest_gaps': [],
        'most_aligned': [],
        'shared_ranked_count': 0,
        'alignment_score': None,
        'alignment_status': 'hidden' if not ranked_visible else 'not_enough_overlap',
        'method': METHOD,
    }
    if not ranked_visible:
        return base

    tracker, catalog = shelf.tracker_model, shelf.catalog_model
    target_rows = (
        db.query(tracker, catalog)
        .join(catalog, getattr(tracker, shelf.join_col) == catalog.pk)
        .filter(
            tracker.user_id == target.pk,
            tracker.on_rankings.is_(True),
            tracker.rank.isnot(None),
        )
        .order_by(tracker.rank.asc())
        .all()
    )
    viewer_trackers = {
        getattr(row, shelf.join_col): row
        for row in db.query(tracker).filter(tracker.user_id == viewer.pk).all()
    }
    base['recommendations'] = [
        _item(
            item,
            their_rank=target_tracker.rank,
            on_your_watchlist=bool(
                viewer_trackers.get(item.pk) and viewer_trackers[item.pk].on_watchlist
            ),
        )
        for target_tracker, item in target_rows
        if not (viewer_trackers.get(item.pk) and viewer_trackers[item.pk].on_rankings)
    ][:RESULT_LIMIT]

    shared = []
    for target_tracker, item in target_rows:
        mine = viewer_trackers.get(item.pk)
        if mine is None or not mine.on_rankings or mine.rank is None:
            continue
        shared.append(
            _item(
                item,
                your_rank=mine.rank,
                their_rank=target_tracker.rank,
                gap=_rank_gap(mine.rank, target_tracker.rank),
            )
        )

    base['shared_ranked_count'] = len(shared)
    base['biggest_gaps'] = sorted(
        shared, key=lambda item: (-item['gap'], item['title'].lower())
    )[:RESULT_LIMIT]
    base['most_aligned'] = sorted(
        shared, key=lambda item: (item['gap'], item['title'].lower())
    )[:RESULT_LIMIT]
    if len(shared) >= MIN_SHARED_FOR_SCORE:
        mean_gap = sum(item['gap'] for item in shared) / len(shared)
        base['alignment_score'] = round(max(0.0, 100 - mean_gap))
        base['alignment_status'] = 'ready'

    if watchlist_visible:
        target_watchlist = (
            db.query(tracker, catalog)
            .join(catalog, getattr(tracker, shelf.join_col) == catalog.pk)
            .filter(
                tracker.user_id == target.pk,
                tracker.on_watchlist.is_(True),
            )
            .order_by(tracker.created_at.desc())
            .all()
        )
        base['common_watchlist'] = [
            _item(item)
            for _, item in target_watchlist
            if viewer_trackers.get(item.pk) and viewer_trackers[item.pk].on_watchlist
        ]
    return base
