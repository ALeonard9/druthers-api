"""
Follows (#276): an asymmetric, unapproved relationship that feeds an activity
feed later (#280) and otherwise grants nothing.

Deliberately separate from the mutual friend graph in
:mod:`app.services.friendships`: a friendship is symmetric and approved, and
unlocks the ``friends`` visibility tier; a follow is neither, and unlocks
nothing beyond what an anonymous visitor already sees. Keeping the two apart
- their own table, their own service module, their own router - is what
stops "follow" from ever becoming a backdoor into friends-only content: there
is no code path from a follow row to a tier ceiling. See
:func:`app.services.visibility.ceiling_for`, which a follow never appears in.

One row per *direction*, unlike a friendship: a follow only ever has a
follower and a followee, so there is no canonical ordering to enforce and no
"who asked" column to carry - the follower always did.
"""


def assert_not_self(follower_pk: int, followee_pk: int) -> None:
    """A user cannot follow themselves - checked before any row is touched."""
    if follower_pk == followee_pk:
        raise ValueError('A user cannot follow themselves')
