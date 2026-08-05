# pylint: disable=missing-function-docstring
"""The direct rank-distance calculation behind comparisons."""

from app.services.comparison import _rank_gap


def test_equal_ranks_have_no_gap():
    assert _rank_gap(14, 14) == 0


def test_gap_is_the_absolute_rank_difference():
    assert _rank_gap(88, 10) == 78
    assert _rank_gap(10, 88) == 78
