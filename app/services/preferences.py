"""
Viewer display preferences (#122).

Today, just how many entries of a ranked list to show by default. Kept
apart from :mod:`app.services.visibility` on purpose: this is a reading
preference the *viewer* controls, not a sharing setting the *owner*
controls, and it never touches the tier ladder.
"""

from enum import StrEnum


class RankedListLength(StrEnum):
    """25/50/100/all. 25 is the default, applied whenever the column is NULL."""

    TWENTY_FIVE = '25'
    FIFTY = '50'
    HUNDRED = '100'
    ALL = 'all'


DEFAULT_RANKED_LIST_LENGTH = RankedListLength.TWENTY_FIVE


def coerce(value) -> RankedListLength:
    """Read a stored length defensively: NULL or anything unrecognised is the default."""
    try:
        return RankedListLength(value)
    except ValueError:
        return DEFAULT_RANKED_LIST_LENGTH
