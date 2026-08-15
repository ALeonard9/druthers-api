"""
Visibility tiers (#274): ``private`` < ``friends`` < ``public``.

Every shelf - and the profile itself - carries one of three tiers instead of
the on/off booleans this replaced. The tier is an enum in Python and a
constrained string in the database (never an integer): a value nobody wrote
on purpose has to be a loud failure, not a silently more permissive setting.

Since #277 the ``friends`` tier is live, and reading a tier is therefore a
two-part question: what the owner set, and who is asking. A viewer-aware
reader resolves the caller's :class:`ViewerRelationship` once, turns it into a
tier ceiling with :func:`ceiling_for`, and asks :func:`admits` per shelf.
:func:`is_public` remains the right question only where there is no viewer at
all - "is this profile shareable", as the owner's own summary asks it.
"""

from enum import StrEnum
from typing import Optional


class VisibilityTier(StrEnum):
    """How widely one shelf - or the whole profile - is shared."""

    PRIVATE = 'private'
    FRIENDS = 'friends'
    PUBLIC = 'public'


class ViewerRelationship(StrEnum):
    """
    Who the caller is to the profile owner (#277).

    One value covers the four framings a client has to render, and the four
    levels of access the API grants. ``NONE`` is an authenticated caller with
    no relationship - deliberately distinct from ``ANONYMOUS``, which is not
    signed in at all, even though the two are served identical shelves.
    """

    ANONYMOUS = 'anonymous'
    NONE = 'none'
    FRIEND = 'friend'
    SELF = 'self'


# New accounts start visible to friends, not private (web#156) - a fresh
# signup is otherwise invisible to the friends who invited them. This one
# constant drives every tier_column() default in app/db/models.py, so the
# profile's own default moves in lockstep with the shelves it fronts and the
# "profile >= most-open shelf" floor invariant holds from account creation.
DEFAULT_TIER = VisibilityTier.FRIENDS

# The DbUser column governing the profile page itself (the ninth setting).
PROFILE_TIER_FIELD = 'visibility_profile'

# Openness ranking. Kept as a lookup rather than relying on declaration order
# so reordering the enum can't quietly reorder the permission model.
_OPENNESS = {
    VisibilityTier.PRIVATE: 0,
    VisibilityTier.FRIENDS: 1,
    VisibilityTier.PUBLIC: 2,
}


def coerce(value) -> VisibilityTier:
    """
    Read a stored tier defensively: NULL or anything unrecognised is private.

    The database CHECK constraint and the SQLAlchemy enum both reject bad
    writes, so this only ever fires on rows that predate a constraint or were
    written out of band. Those resolve *closed*.
    """
    try:
        return VisibilityTier(value)
    except ValueError:
        return VisibilityTier.PRIVATE


def resolve_tier(default_privacy, override) -> VisibilityTier:
    """Return a shelf override or, when absent, its global default tier."""
    return coerce(default_privacy if override is None else override)


def openness(value) -> int:
    """Comparable rank of a tier: private 0, friends 1, public 2."""
    return _OPENNESS[coerce(value)]


def is_public(value) -> bool:
    """True only for ``public`` - friends and junk read as private."""
    return coerce(value) is VisibilityTier.PUBLIC


def most_open(values) -> VisibilityTier:
    """The most permissive tier in ``values`` (private when empty)."""
    return max(
        (coerce(value) for value in values),
        key=openness,
        default=VisibilityTier.PRIVATE,
    )


def covers(profile: Optional[str], shelf: Optional[str]) -> bool:
    """True when a profile tier is at least as open as a shelf's."""
    return openness(profile) >= openness(shelf)


# The least-open tier each relationship is served. Anything at or above this
# tier reaches the viewer; anything below it does not exist as far as they are
# concerned. A relationship missing from this table is a KeyError rather than
# a default, so a new relationship cannot silently inherit an access level.
_CEILING = {
    ViewerRelationship.SELF: VisibilityTier.PRIVATE,
    ViewerRelationship.FRIEND: VisibilityTier.FRIENDS,
    ViewerRelationship.NONE: VisibilityTier.PUBLIC,
    ViewerRelationship.ANONYMOUS: VisibilityTier.PUBLIC,
}


def ceiling_for(relationship: ViewerRelationship) -> VisibilityTier:
    """
    The tier ceiling a viewer is served, resolved from their relationship.

    Owners see everything of their own; accepted friends additionally see
    ``friends``; everybody else - signed in or not - sees ``public`` only.
    Following (#276) grants nothing, so it never appears here.
    """
    return _CEILING[relationship]


def admits(ceiling: VisibilityTier, tier: Optional[str]) -> bool:
    """
    True when a viewer at ``ceiling`` may be served something at ``tier``.

    The same comparison :func:`covers` makes, named for the authorization
    question so a call site cannot get the operands backwards unnoticed.
    """
    return covers(tier, ceiling)
