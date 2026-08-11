"""
Database operations for follows (#276).

Everything that needs the mapped :class:`~app.db.models.DbFollow` lives
here, the same split :mod:`app.db.db_friendship` uses for the friend graph.

:func:`is_following` is the one call site with any authorization weight, and
it never carries any *visibility* weight: nothing here is consulted by
:mod:`app.services.visibility`, so a caller cannot turn a follow into a
tier ceiling by routing it through this module differently.
"""

from typing import List, Optional, Tuple

from sqlalchemy.orm import Session

from app.db.models import DbFollow, DbUser


def find(db: Session, follower_pk: int, followee_pk: int) -> Optional[DbFollow]:
    """The single follow row for this direction, or ``None``."""
    return (
        db.query(DbFollow)
        .filter(
            DbFollow.follower_id == follower_pk,
            DbFollow.followee_id == followee_pk,
        )
        .first()
    )


def is_following(db: Session, follower_pk: int, followee_pk: int) -> bool:
    """
    True when ``follower_pk`` already follows ``followee_pk``.

    Self is always False without a query — a user is never following
    themselves, the same shape :func:`app.db.db_friendship.are_friends`
    uses for the self case.
    """
    if follower_pk == followee_pk:
        return False
    return (
        db.query(DbFollow.pk)
        .filter(
            DbFollow.follower_id == follower_pk,
            DbFollow.followee_id == followee_pk,
        )
        .first()
        is not None
    )


def count_followers(db: Session, followee_pk: int) -> int:
    """Return the number of follow rows directed at ``followee_pk``."""
    return db.query(DbFollow).filter(DbFollow.followee_id == followee_pk).count()


def list_following(db: Session, user_pk: int) -> List[Tuple[DbFollow, DbUser]]:
    """Everyone this user follows, each paired with the followee's row."""
    return (
        db.query(DbFollow, DbUser)
        .join(DbUser, DbUser.pk == DbFollow.followee_id)
        .filter(DbFollow.follower_id == user_pk)
        .order_by(DbFollow.followed_at.desc())
        .all()
    )


def list_followers(db: Session, user_pk: int) -> List[Tuple[DbFollow, DbUser]]:
    """Everyone following this user, each paired with the follower's row."""
    return (
        db.query(DbFollow, DbUser)
        .join(DbUser, DbUser.pk == DbFollow.follower_id)
        .filter(DbFollow.followee_id == user_pk)
        .order_by(DbFollow.followed_at.desc())
        .all()
    )
