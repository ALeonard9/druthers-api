# pylint: disable=missing-function-docstring
"""The ranking normalization behind comparison scores."""

from app.services.comparison import _position


def test_top_weighting_expands_differences_near_the_favorites():
    # Both spans are ten places long, but the top-of-list span deliberately
    # counts for more than the same raw distance near the bottom.
    top_gap = _position(11, 100) - _position(1, 100)
    bottom_gap = _position(100, 100) - _position(90, 100)
    assert top_gap > bottom_gap


def test_one_item_list_has_a_stable_best_position():
    assert _position(1, 1) == 0.0
