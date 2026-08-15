"""Compare the signed-in user's shelves with one visible profile (#281)."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.oauth2 import get_current_user
from app.db.database import get_db
from app.db.models import DbUser
from app.services.comparison import compare_shelf
from app.services.profile_access import (
    outgoing_friend_request_state,
    viewer_relationship,
)
from app.services.shelves import SHELVES, Shelf
from app.services.tracker_rules import default_completed_at
from app.services.visibility import admits, ceiling_for, resolve_tier

router = APIRouter(prefix='/v1', tags=['Comparison'])


class SaveRecommendation(BaseModel):
    """The destination list; ranking placement happens later in its normal UI."""

    destination: Literal['watchlist', 'rankings']


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail='No visible profile here',
        headers={'Vary': 'Authorization'},
    )


def _target_access(db: Session, viewer: DbUser, handle: str):
    target = db.query(DbUser).filter(DbUser.handle == handle.lower()).first()
    if target is None or target.pk == viewer.pk:
        raise _not_found()
    relationship = viewer_relationship(db, target, viewer)
    ceiling = ceiling_for(relationship)
    if not admits(ceiling, target.visibility_profile):
        raise _not_found()
    visible = [
        shelf
        for shelf in SHELVES
        if admits(
            ceiling,
            resolve_tier(
                target.default_privacy, getattr(target, shelf.visibility_tier)
            ),
        )
    ]
    if not visible:
        raise _not_found()
    return target, relationship, ceiling


@router.get('/users/me/comparison/{handle}')
def compare_with_user(
    handle: str,
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
):
    """Return the four domain comparisons visible to this viewer."""
    viewer = current_user[0]
    target, relationship, ceiling = _target_access(db, viewer, handle)
    friend_request_state = outgoing_friend_request_state(
        db, target, viewer, relationship
    )
    return {
        'handle': target.handle,
        'display_name': target.display_name,
        'relationship': relationship.value,
        'friend_request_state': friend_request_state,
        'domains': [
            compare_shelf(db, viewer, target, shelf, ceiling) for shelf in SHELVES
        ],
    }


def _shelf(category: str) -> Shelf:
    found = next((shelf for shelf in SHELVES if shelf.category == category), None)
    if found is None:
        raise HTTPException(status_code=404, detail='Domain not found')
    return found


@router.post(
    '/users/me/comparison/{handle}/{category}/{item_id}',
    status_code=status.HTTP_201_CREATED,
)
def save_recommendation(  # pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals
    handle: str,
    category: str,
    item_id: str,
    request: SaveRecommendation,
    db: Session = Depends(get_db),
    current_user: list = Depends(get_current_user),
):
    """Save a visible ranked recommendation and retain its first source."""
    viewer = current_user[0]
    target, _, ceiling = _target_access(db, viewer, handle)
    shelf = _shelf(category)
    if not admits(
        ceiling,
        resolve_tier(target.default_privacy, getattr(target, shelf.visibility_tier)),
    ):
        raise _not_found()

    tracker_model, catalog_model = shelf.tracker_model, shelf.catalog_model
    catalog = db.query(catalog_model).filter(catalog_model.id == item_id).first()
    if catalog is None:
        raise HTTPException(status_code=404, detail='Item not found')
    target_tracker = (
        db.query(tracker_model)
        .filter(
            tracker_model.user_id == target.pk,
            getattr(tracker_model, shelf.join_col) == catalog.pk,
            tracker_model.on_rankings.is_(True),
            tracker_model.rank.isnot(None),
        )
        .first()
    )
    if target_tracker is None:
        raise _not_found()

    mine = (
        db.query(tracker_model)
        .filter(
            tracker_model.user_id == viewer.pk,
            getattr(tracker_model, shelf.join_col) == catalog.pk,
        )
        .first()
    )
    created = mine is None
    if mine is None:
        mine = tracker_model(
            user_id=viewer.pk,
            source_user_id=target.pk,
            **{shelf.join_col: catalog.pk},
        )
        db.add(mine)

    was_on_rankings = bool(mine.on_rankings)
    if request.destination == 'watchlist':
        mine.on_watchlist = True
        mine.on_rankings = False
        mine.rank = None
        mine.ranked_at = None
    else:
        mine.on_watchlist = False
        mine.on_rankings = True
        mine.rank = None
        mine.ranked_at = None
        default_completed_at(mine, was_on_rankings)
    db.commit()
    db.refresh(mine)
    return {
        'id': mine.id,
        'item_id': catalog.id,
        'destination': request.destination,
        'source_handle': mine.source_handle,
        'source_recorded': created,
    }
