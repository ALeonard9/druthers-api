"""
Mutual friendships (#275): the graph the ``friends`` visibility tier resolves
against.

One row per *relationship*, not one per direction. The two user ids are
stored in canonical order (``user_low_id < user_high_id``) with a separate
column recording who asked, so "are these two friends" is a single indexed
lookup and the two directions can never disagree with each other — there is
only one direction to disagree with.

This module is deliberately pure: the enum and the ordering rule live here so
``app.db.models`` can import them (the same arrangement
:mod:`app.services.visibility` has), while every query that needs the mapped
class lives in :mod:`app.db.db_friendship`.
"""

from enum import StrEnum
from typing import Tuple


class FriendshipStatus(StrEnum):
    """
    Where a relationship sits: asked for, or agreed to.

    There is no ``declined`` member on purpose. A declined request deletes its
    row, which (a) keeps a decline from being readable as a standing snub and
    (b) lets the pair try again later without a tombstone in the way. The
    consequence to remember is that "no row" means *no relationship*, never
    "previously refused".
    """

    PENDING = 'pending'
    ACCEPTED = 'accepted'


DEFAULT_STATUS = FriendshipStatus.PENDING


def canonical_pair(one_pk: int, other_pk: int) -> Tuple[int, int]:
    """
    The two user ids as ``(low, high)`` — the order every row is stored in.

    Every read and write goes through this, so a relationship has exactly one
    representation no matter which side of it is asking.
    """
    if one_pk == other_pk:
        raise ValueError('A user cannot be in a friendship with themselves')
    return (one_pk, other_pk) if one_pk < other_pk else (other_pk, one_pk)


def other_party(user_low_id: int, user_high_id: int, viewer_pk: int) -> int:
    """The id on the far side of a relationship from ``viewer_pk``."""
    return user_high_id if viewer_pk == user_low_id else user_low_id
