"""Social context utilities for items."""

from typing import List


from sqlalchemy.orm import Session

from app.db.db_friendship import friend_pks
from app.db.models import DbFollow, DbUser
from app.services.shelves import Shelf
from app.services.visibility import (
    ViewerRelationship,
    admits,
    ceiling_for,
    resolve_tier,
)

# pylint: disable=too-many-locals
# pylint: disable=too-many-locals


def get_item_social_context(
    db: Session, viewer: DbUser, shelf: Shelf, item_id: str
) -> List[dict]:
    """Get the social context for an item."""
    tracker_model = shelf.tracker_model
    catalog_model = shelf.catalog_model

    # 1. Resolve item
    catalog_item = db.query(catalog_model).filter(catalog_model.id == item_id).first()
    if not catalog_item:
        return []

    # 2. Get friends and followees pks
    f_pks = set(friend_pks(db, viewer.pk))
    following = (
        db.query(DbFollow.followee_id).filter(DbFollow.follower_id == viewer.pk).all()
    )
    followee_pks = {f[0] for f in following}
    target_pks = f_pks | followee_pks

    if not target_pks:
        return []

    # 3. Get trackers for these users
    trackers = (
        db.query(tracker_model, DbUser)
        .join(DbUser, DbUser.pk == tracker_model.user_id)
        .filter(
            getattr(tracker_model, shelf.join_col) == catalog_item.pk,
            tracker_model.user_id.in_(target_pks),
            DbUser.disabled_at.is_(None),
        )
        .all()
    )

    results = []
    for tracker, user in trackers:
        # Determine relationship
        if user.pk in f_pks:
            rel = ViewerRelationship.FRIEND
            display_rel = 'friends'
        else:
            rel = ViewerRelationship.NONE
            display_rel = 'follows'

        ceiling = ceiling_for(rel)
        if not admits(ceiling, user.visibility_profile):
            continue

        shelf_tier = resolve_tier(
            user.default_privacy, getattr(user, shelf.visibility_tier)
        )
        if not admits(ceiling, shelf_tier):
            continue

        watchlist_tier = resolve_tier(
            user.default_privacy, getattr(user, shelf.watchlist_visibility_tier)
        )
        notes_tier = resolve_tier(
            shelf_tier, getattr(user, shelf.notes_visibility_tier)
        )

        # Build payload
        item_data = {
            'handle': user.handle,
            'display_name': user.display_name,
            'relationship': display_rel,
            'rank': tracker.rank if tracker.on_rankings else None,
            'on_watchlist': (
                bool(tracker.on_watchlist) if admits(ceiling, watchlist_tier) else False
            ),
            'notes': (
                tracker.notes if tracker.notes and admits(ceiling, notes_tier) else None
            ),
        }
        results.append(item_data)

    return results
