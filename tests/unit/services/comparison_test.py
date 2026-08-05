# pylint: disable=missing-function-docstring
"""The direct rank-distance calculation behind comparisons."""

from app.services.comparison import _alignment_score, _rank_gap


def test_equal_ranks_have_no_gap():
    assert _rank_gap(14, 14) == 0


def test_gap_is_the_absolute_rank_difference():
    assert _rank_gap(88, 10) == 78
    assert _rank_gap(10, 88) == 78


def test_alignment_scales_average_gap_against_longer_shelf_span():
    assert _alignment_score([4, 4, 2, 2, 0], longer_shelf_count=7) == 60
    assert _alignment_score([0, 0, 0, 0, 0], longer_shelf_count=100) == 100
