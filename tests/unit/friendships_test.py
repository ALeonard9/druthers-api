"""
Unit tests for the pure half of the friend graph (#275): the canonical
ordering that lets one row stand in for two directions.
"""

import pytest

from app.services.friendships import (
    DEFAULT_STATUS,
    FriendshipStatus,
    canonical_pair,
    other_party,
)


def test_canonical_pair_is_order_independent():
    """Both seats produce the same (low, high), which is the whole point."""
    assert canonical_pair(3, 9) == (3, 9)
    assert canonical_pair(9, 3) == (3, 9)


def test_canonical_pair_rejects_self():
    """A self-friendship is unrepresentable here as well as in the CHECK."""
    with pytest.raises(ValueError):
        canonical_pair(7, 7)


def test_other_party_reads_from_either_seat():
    """Whichever column the viewer occupies, the friend is the other one."""
    assert other_party(3, 9, viewer_pk=3) == 9
    assert other_party(3, 9, viewer_pk=9) == 3


def test_status_values_are_the_two_the_schema_allows():
    """
    A third status would need a migration — the CHECK constraint in
    c9a2e7f31b04 lists exactly these. Declining deletes its row instead.
    """
    assert [status.value for status in FriendshipStatus] == ['pending', 'accepted']
    assert DEFAULT_STATUS is FriendshipStatus.PENDING
