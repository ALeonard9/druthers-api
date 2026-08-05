"""Relationship resolution shared by viewer-aware profile surfaces."""

from typing import Optional

from sqlalchemy.orm import Session

from app.db.db_friendship import are_friends
from app.db.models import DbUser
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
