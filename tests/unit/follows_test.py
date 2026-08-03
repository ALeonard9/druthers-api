"""
Unit tests for the pure half of the follow graph (#276).
"""

import pytest

from app.services.follows import assert_not_self


def test_assert_not_self_passes_for_distinct_users():
    """Two different users are a legitimate follow; nothing is raised."""
    assert assert_not_self(3, 9) is None


def test_assert_not_self_rejects_a_self_follow():
    """Following yourself is rejected here, before any row is built."""
    with pytest.raises(ValueError):
        assert_not_self(7, 7)
