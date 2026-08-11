# pylint: disable=missing-module-docstring, missing-function-docstring
import pytest

from app.services.visibility import (
    ViewerRelationship,
    VisibilityTier,
    admits,
    ceiling_for,
    coerce,
    covers,
    is_public,
    most_open,
    openness,
    resolve_tier,
)


def test_tiers_are_ordered_private_friends_public():
    assert openness(VisibilityTier.PRIVATE) < openness(VisibilityTier.FRIENDS)
    assert openness(VisibilityTier.FRIENDS) < openness(VisibilityTier.PUBLIC)


@pytest.mark.parametrize('value', [None, '', 'everyone', 'PUBLIC', 0, True])
def test_unrecognised_values_read_as_private(value):
    # The failure direction that matters: junk must never resolve open.
    assert coerce(value) is VisibilityTier.PRIVATE
    assert is_public(value) is False


def test_only_public_is_public():
    assert is_public(VisibilityTier.PUBLIC) is True
    assert is_public(VisibilityTier.FRIENDS) is False
    assert is_public(VisibilityTier.PRIVATE) is False
    # Strings off the wire resolve the same way as enum members.
    assert is_public('public') is True


def test_shelf_override_resolution_uses_default_only_when_absent():
    assert resolve_tier('friends', None) is VisibilityTier.FRIENDS
    assert resolve_tier('private', 'public') is VisibilityTier.PUBLIC
    # A malformed override must still resolve closed, never to the default.
    assert resolve_tier('public', 'everyone') is VisibilityTier.PRIVATE


def test_most_open_picks_the_widest_tier():
    assert most_open([]) is VisibilityTier.PRIVATE
    assert most_open(['private', 'private']) is VisibilityTier.PRIVATE
    assert most_open(['private', 'friends']) is VisibilityTier.FRIENDS
    assert most_open(['friends', 'public', 'private']) is VisibilityTier.PUBLIC


def test_covers_allows_equal_and_wider_profiles():
    assert covers('public', 'friends') is True
    assert covers('friends', 'friends') is True
    assert covers('public', 'public') is True
    assert covers('friends', 'public') is False
    assert covers('private', 'friends') is False


def test_every_relationship_has_a_ceiling():
    # A relationship with no entry would KeyError at request time; keeping the
    # table total is the point of the assertion.
    assert {ceiling_for(rel) for rel in ViewerRelationship} == {
        VisibilityTier.PRIVATE,
        VisibilityTier.FRIENDS,
        VisibilityTier.PUBLIC,
    }


def test_ceilings_grant_exactly_what_the_tiers_promise():
    assert ceiling_for(ViewerRelationship.SELF) is VisibilityTier.PRIVATE
    assert ceiling_for(ViewerRelationship.FRIEND) is VisibilityTier.FRIENDS
    # Signed in but unrelated is served exactly what a stranger is served.
    assert ceiling_for(ViewerRelationship.NONE) is VisibilityTier.PUBLIC
    assert ceiling_for(ViewerRelationship.ANONYMOUS) is VisibilityTier.PUBLIC


@pytest.mark.parametrize(
    'relationship, expected',
    [
        (ViewerRelationship.ANONYMOUS, [False, False, True]),
        (ViewerRelationship.NONE, [False, False, True]),
        (ViewerRelationship.FRIEND, [False, True, True]),
        (ViewerRelationship.SELF, [True, True, True]),
    ],
)
def test_admits_grants_the_tier_and_everything_above_it(relationship, expected):
    ceiling = ceiling_for(relationship)
    assert [admits(ceiling, tier) for tier in VisibilityTier] == expected


@pytest.mark.parametrize('value', [None, '', 'everyone', 'PUBLIC'])
def test_junk_tiers_are_served_to_nobody_but_the_owner(value):
    # A tier nobody wrote on purpose resolves private, so only the ceiling
    # that already sees private content gets it.
    assert admits(ceiling_for(ViewerRelationship.ANONYMOUS), value) is False
    assert admits(ceiling_for(ViewerRelationship.NONE), value) is False
    assert admits(ceiling_for(ViewerRelationship.FRIEND), value) is False
    assert admits(ceiling_for(ViewerRelationship.SELF), value) is True
