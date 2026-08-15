"""Relationship resolution shared by viewer-aware profile surfaces."""

from typing import Optional

from sqlalchemy.orm import Session

from app.db.db_friendship import are_friends, friendship_between
from app.db.models import DbUser
from app.services.friendships import FriendshipStatus
from app.services.visibility import ViewerRelationship


def viewer_relationship(
    db: Session, owner: DbUser, viewer: Optional[DbUser]
) -> ViewerRelationship:
    """Resolve the caller once before any shelf visibility checks."""
    if viewer is None:
        return ViewerRelationship.ANONYMOUS
    if viewer.pk == owner.pk:
        return ViewerRelationship.SELF
    if are_friends(db, viewer.pk, owner.pk):
        return ViewerRelationship.FRIEND
    return ViewerRelationship.NONE


def outgoing_friend_request_state(
    db: Session,
    owner: DbUser,
    viewer: Optional[DbUser],
    relationship: ViewerRelationship,
) -> str:
    """The request state the viewer's friend button should render."""
    if relationship is ViewerRelationship.FRIEND:
        return 'friends'
    if viewer is None or relationship is not ViewerRelationship.NONE:
        return 'none'
    friendship = friendship_between(db, viewer.pk, owner.pk)
    if (
        friendship is not None
        and friendship.status is FriendshipStatus.PENDING
        and friendship.requested_by_id == viewer.pk
    ):
        return 'pending'
    return 'none'
