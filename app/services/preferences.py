"""
Viewer display preferences (#122).

How many entries of a ranked list to show by default, and which time zone
the caller's own hours are rendered in. Kept apart from
:mod:`app.services.visibility` on purpose: these are reading preferences the
*viewer* controls, not sharing settings the *owner* controls, and they never
touch the tier ladder.
"""

from enum import StrEnum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from app.config import get_settings


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


def is_valid_time_zone(value: str) -> bool:
    """Whether ``value`` names a zone this interpreter's tzdata can resolve."""
    if not isinstance(value, str) or not value:
        return False
    # ``available_timezones`` is the membership test; ZoneInfo alone accepts
    # some paths that are not real zones on platforms with a system tzdb.
    if value not in available_timezones():
        return False
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True


def coerce_time_zone(value) -> str:
    """
    Read a stored IANA zone defensively, falling back to the deployment's.

    NULL means "never chosen" -- the overwhelming majority of rows -- and
    reads as ``TIME_ZONE`` from the environment (#322), so a fleet-wide
    default can still be moved without touching a single user row. An
    unrecognised string falls back the same way rather than raising: this
    runs on every read of every preference, and a zone that disappeared from
    tzdata between releases must not take the endpoint down with it.
    """
    if isinstance(value, str) and is_valid_time_zone(value):
        return value
    return get_settings().time_zone


def time_zone_info(value) -> ZoneInfo:
    """The :class:`ZoneInfo` for a stored zone, defaulted like :func:`coerce_time_zone`."""
    return ZoneInfo(coerce_time_zone(value))
