"""
Database operations for the friend graph (#275).

Everything that needs the mapped :class:`~app.db.models.DbFriendship` lives
here rather than in :mod:`app.services.friendships`, which ``app.db.models``
imports. The split is what keeps the import graph acyclic; it is the same
arrangement ``db_user`` / ``model_schemas`` already use.

:func:`are_friends` is the entry point the ``friends`` visibility tier is
meant to call (#277) - one indexed lookup, no knowledge of canonical ordering
required at the call site.
"""

from typing import List, Optional, Tuple

from sqlalchemy import case, or_
from sqlalchemy.orm import Session

from app.db.models import DbFriendship, DbUser
from app.services.friendships import FriendshipStatus, canonical_pair


def friendship_between(
    db: Session, one_pk: int, other_pk: int
) -> Optional[DbFriendship]:
    """
    The single row for this pair, whatever its status, or ``None``.

    Returns pending rows too - callers that only care about real friendships
    want :func:`are_friends`.
    """
    if one_pk == other_pk:
        return None
    low, high = canonical_pair(one_pk, other_pk)
    return (
        db.query(DbFriendship)
        .filter(
            DbFriendship.user_low_id == low,
            DbFriendship.user_high_id == high,
        )
        .first()
    )


def are_friends(db: Session, one_pk: int, other_pk: int) -> bool:
    """
    True when these two users have an *accepted* friendship.

    The question the ``friends`` visibility tier asks (#277). A pending
    request is not a friendship, and a user is not their own friend - the
    self case is False so a viewer looking at their own profile is served by
    ownership, never by an accidental friends-with-self edge.
    """
    if one_pk == other_pk:
        return False
    low, high = canonical_pair(one_pk, other_pk)
    return (
        db.query(DbFriendship.pk)
        .filter(
            DbFriendship.user_low_id == low,
            DbFriendship.user_high_id == high,
            DbFriendship.status == FriendshipStatus.ACCEPTED,
        )
        .first()
        is not None
    )


def friend_pks(db: Session, user_pk: int) -> List[int]:
    """Every user pk this user is accepted friends with."""
    rows = (
        db.query(DbFriendship.user_low_id, DbFriendship.user_high_id)
        .filter(
            or_(
                DbFriendship.user_low_id == user_pk,
                DbFriendship.user_high_id == user_pk,
            ),
            DbFriendship.status == FriendshipStatus.ACCEPTED,
        )
        .all()
    )
    return [high if low == user_pk else low for low, high in rows]


def _involving(user_pk: int):
    """Filter matching rows on either side of the relationship."""
    return or_(
        DbFriendship.user_low_id == user_pk,
        DbFriendship.user_high_id == user_pk,
    )


def _other_user_id(user_pk: int):
    """SQL expression for the far-side user pk, from ``user_pk``'s seat."""
    return case(
        (DbFriendship.user_low_id == user_pk, DbFriendship.user_high_id),
        else_=DbFriendship.user_low_id,
    )


def list_with_other_party(
    db: Session,
    user_pk: int,
    status: FriendshipStatus,
    requested_by_me: Optional[bool] = None,
) -> List[Tuple[DbFriendship, DbUser]]:
    """
    Rows involving this user, each paired with the *other* user's row.

    Joining on a CASE keeps this to one query from either seat, so listing
    friends does not fan out into a lookup per row. ``requested_by_me``
    narrows pending rows to one direction: True for requests this user sent,
    False for ones they received, None for both.
    """
    query = (
        db.query(DbFriendship, DbUser)
        .join(DbUser, DbUser.pk == _other_user_id(user_pk))
        .filter(_involving(user_pk), DbFriendship.status == status)
    )
    if requested_by_me is True:
        query = query.filter(DbFriendship.requested_by_id == user_pk)
    elif requested_by_me is False:
        query = query.filter(DbFriendship.requested_by_id != user_pk)
    return query.order_by(DbFriendship.requested_at.desc()).all()


def get_for_user(
    db: Session,
    user_pk: int,
    friendship_id: str,
    status: FriendshipStatus,
) -> Optional[Tuple[DbFriendship, DbUser]]:
    """
    One row by public id, but only if this user is a party to it.

    Scoping the lookup to the caller is what makes a wrong or guessed id
    indistinguishable from someone else's row: both simply return ``None``,
    and the router turns both into the same 404.
    """
    return (
        db.query(DbFriendship, DbUser)
        .join(DbUser, DbUser.pk == _other_user_id(user_pk))
        .filter(
            DbFriendship.id == friendship_id,
            _involving(user_pk),
            DbFriendship.status == status,
        )
        .first()
    )
