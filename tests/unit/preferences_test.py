# pylint: disable=missing-function-docstring
"""Preference coercion (:mod:`app.services.preferences`)."""

from zoneinfo import ZoneInfo

import pytest

from app.config import get_settings
from app.services import preferences
from app.services.preferences import RankedListLength


@pytest.mark.parametrize(
    'stored, expected',
    [
        ('25', RankedListLength.TWENTY_FIVE),
        ('all', RankedListLength.ALL),
        (None, RankedListLength.TWENTY_FIVE),
        ('37', RankedListLength.TWENTY_FIVE),
    ],
)
def test_coerce_length(stored, expected):
    assert preferences.coerce(stored) == expected


@pytest.mark.parametrize(
    'value',
    ['UTC', 'America/Chicago', 'Asia/Tokyo', 'Australia/Sydney'],
)
def test_real_zones_are_valid(value):
    assert preferences.is_valid_time_zone(value)


@pytest.mark.parametrize(
    'value',
    ['Mars/Olympus_Mons', '', 'not a zone', 'America/Chicago/extra', None, 5],
)
def test_junk_is_not_a_valid_zone(value):
    assert not preferences.is_valid_time_zone(value)


def test_stored_zone_is_returned_as_is():
    assert preferences.coerce_time_zone('Asia/Tokyo') == 'Asia/Tokyo'


@pytest.mark.parametrize('stored', [None, '', 'Mars/Olympus_Mons', 17])
def test_unset_or_unusable_falls_back_to_the_deployment_zone(stored):
    """
    The fallback is what lets the column stay NULL for almost every row.

    It also has to survive a zone that tzdata dropped between releases --
    this runs on every preference read, and raising there would take the
    endpoint down for a value that was legal when it was written.
    """
    assert preferences.coerce_time_zone(stored) == get_settings().time_zone


def test_time_zone_info_resolves_to_a_real_zone():
    assert preferences.time_zone_info('Asia/Tokyo') == ZoneInfo('Asia/Tokyo')
    assert preferences.time_zone_info(None) == ZoneInfo(get_settings().time_zone)
