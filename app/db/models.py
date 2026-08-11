"""
This module defines the database models.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import backref, relationship

from app.db.database import Base
from app.services.friendships import DEFAULT_STATUS, FriendshipStatus
from app.services.preferences import RankedListLength
from app.services.visibility import DEFAULT_TIER, VisibilityTier


def tier_column(name: str) -> Column:
    """
    One ``private | friends | public`` column on ``users``.

    Stored as a VARCHAR with its own CHECK constraint rather than an integer
    or a native PG enum: a stray value fails loudly at write time, and a
    rollback of the owning migration doesn't leave an orphaned type behind.
    The constraint is named per column because Postgres scopes CHECK names to
    the table, so nine columns cannot share one name.
    """
    return Column(
        name,
        Enum(
            VisibilityTier,
            name=f"ck_users_{name}",
            native_enum=False,
            create_constraint=True,
            length=16,
            values_callable=lambda members: [member.value for member in members],
        ),
        nullable=False,
        default=DEFAULT_TIER,
        server_default=DEFAULT_TIER.value,
    )


class DBBaseModel(Base):
    """
    Base model that includes common fields for all tables.
    """

    __abstract__ = True  # This class won't be created as a table in the database
    # Primary key never shared externally/ only used for relationships and indexing
    pk = Column(Integer, primary_key=True, index=True, autoincrement=True)
    # Ok to include in API responses
    id = Column(String, default=lambda: str(uuid.uuid4()), index=True, unique=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class DbUser(DBBaseModel):
    """
    Database model for users.
    """

    __tablename__ = 'users'
    email = Column(String, unique=True)
    display_name = Column(String(length=30))
    user_group = Column(String, default='user')
    password = Column(String)

    # --- Visibility (#143, tiered in #274): everything is private by
    # default. Anything non-private needs a handle (druthers.io/u/<handle>);
    # only ranked lists and opted-in watchlists are ever exposed.

    handle = Column(String(length=30), unique=True, index=True, nullable=True)

    # The ninth setting: the profile page itself. Invariant enforced in
    # router_visibility — this is always at least as open as the most-open
    # shelf below, so no shelf can be reachable through a profile that is
    # more closed than the shelf.
    visibility_profile = tier_column('visibility_profile')

    visibility_movies = tier_column('visibility_movies')
    visibility_tv = tier_column('visibility_tv')
    visibility_books = tier_column('visibility_books')
    visibility_games = tier_column('visibility_games')

    # Watchlist visibility (#236): independent of the ranked-list tiers
    # above. A category's watchlist is only served when this tier AND the
    # matching ranked-list tier both admit the viewer.
    visibility_watchlist_movies = tier_column('visibility_watchlist_movies')
    visibility_watchlist_tv = tier_column('visibility_watchlist_tv')
    visibility_watchlist_books = tier_column('visibility_watchlist_books')
    visibility_watchlist_games = tier_column('visibility_watchlist_games')

    # Sharing control for the friends/follows activity feed (#280). This is
    # independent of shelf tiers: those still authorize every event at read
    # time, while this switch lets the owner withdraw all contributions at
    # once. Existing users participate until they explicitly opt out.
    share_activity = Column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text('true'),
    )

    # --- Display preferences (#122). Viewer-controlled, not visibility —
    # how much of a ranked list to read, remembered across sessions and
    # devices. NULL means "unset", read as the default (25) everywhere.
    ranked_list_length = Column(
        Enum(
            RankedListLength,
            name='ck_users_ranked_list_length',
            native_enum=False,
            create_constraint=True,
            length=4,
            values_callable=lambda members: [member.value for member in members],
        ),
        nullable=True,
    )

    # Which IANA zone this user's own hours are rendered in — the greeting,
    # the schedule's idea of "today", anything that reads as a wall clock.
    # NULL means "never chosen" and reads as the deployment's TIME_ZONE
    # (#322), so the fleet-wide default can move without rewriting rows.
    # Unconstrained on purpose: the tzdb gains and drops zone names between
    # releases, and a CHECK would turn that into failed writes.
    time_zone = Column(String(64), nullable=True)

    # First-time onboarding state (#135).
    onboarding_completed = Column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text('false'),
    )


class DbApiKey(DBBaseModel):
    """
    Long-lived API key for programmatic access (MCP servers, crons).

    Only the SHA-256 hash of the secret is stored; the plaintext is shown
    exactly once at creation. ``prefix`` is a short display hint so a user
    can tell keys apart in a list.
    """

    __tablename__ = 'api_keys'
    user_id = Column(Integer, ForeignKey('users.pk'), nullable=False)
    name = Column(String(length=60), nullable=False)
    key_hash = Column(String(length=64), unique=True, index=True, nullable=False)
    prefix = Column(String(length=12), nullable=False)
    last_used_at = Column(DateTime, nullable=True)

    user = relationship('DbUser', backref='api_keys')


class DbRefreshToken(DBBaseModel):
    """
    Long-lived, revocable browser-session credential (#246).

    Opaque and random rather than a JWT: the point of choosing a refresh flow
    over a longer-lived access token was individual revocation, which needs
    server-side state. Only the SHA-256 hash is stored, so a database leak
    doesn't hand over usable sessions.

    ``family_id`` ties every token minted by rotating an earlier one back to
    the original sign-in. Presenting an already-rotated token means it leaked
    (or was replayed), and the whole family dies with it.
    """

    __tablename__ = 'refresh_tokens'
    user_id = Column(
        Integer,
        ForeignKey('users.pk', ondelete='CASCADE'),
        nullable=False,
        index=True,
    )
    token_hash = Column(String(length=64), unique=True, index=True, nullable=False)
    family_id = Column(String, nullable=False, index=True)
    expires_at = Column(DateTime, nullable=False)
    # Set together on rotation; kept apart so a revoked-but-unused token
    # (sign-out) is distinguishable from one that was spent.
    revoked_at = Column(DateTime, nullable=True)
    used_at = Column(DateTime, nullable=True)

    # Sessions are meaningless without their owner, so deleting a user takes
    # their tokens with them rather than orphaning rows on a NOT NULL column.
    user = relationship(
        'DbUser',
        backref=backref('refresh_tokens', cascade='all, delete-orphan'),
    )


class DbFriendship(DBBaseModel):
    """
    One mutual friendship — or the request that may become one (#275).

    **One row per relationship, not two.** The pair is stored in canonical
    order (``user_low_id < user_high_id``, enforced by a CHECK) with a
    ``UNIQUE`` across the two columns, so a pair can hold at most one row and
    "are these two friends" is one indexed lookup rather than an OR over two
    mirrored rows that could drift apart. ``requested_by_id`` is what the
    second row would otherwise have carried: it says which side asked, which
    is all the direction the model needs.

    ``responded_at`` is NULL exactly while ``status`` is ``pending``. A
    decline or a cancel deletes the row rather than recording a terminal
    status — see :class:`~app.services.friendships.FriendshipStatus`.

    Rows are meaningless without both parties, so deleting a user takes their
    friendships with them.
    """

    __tablename__ = 'friendships'
    __table_args__ = (
        UniqueConstraint('user_low_id', 'user_high_id', name='uq_friendships_pair'),
        # The canonical ordering is the whole reason one row can stand in for
        # two directions, so the database — not just the service layer —
        # refuses a row that breaks it.
        CheckConstraint(
            'user_low_id < user_high_id', name='ck_friendships_canonical_order'
        ),
    )

    user_low_id = Column(
        Integer,
        ForeignKey('users.pk', ondelete='CASCADE'),
        nullable=False,
        index=True,
    )
    user_high_id = Column(
        Integer,
        ForeignKey('users.pk', ondelete='CASCADE'),
        nullable=False,
        index=True,
    )
    # Which of the two above sent the request. Not a third party: a CHECK
    # can't express "one of these two columns" portably, so the service layer
    # is what guarantees it.
    requested_by_id = Column(
        Integer,
        ForeignKey('users.pk', ondelete='CASCADE'),
        nullable=False,
    )
    status = Column(
        Enum(
            FriendshipStatus,
            name='ck_friendships_status',
            native_enum=False,
            create_constraint=True,
            length=16,
            values_callable=lambda members: [member.value for member in members],
        ),
        nullable=False,
        default=DEFAULT_STATUS,
        server_default=DEFAULT_STATUS.value,
    )
    # Kept explicitly rather than leaning on created_at/updated_at: those are
    # row bookkeeping and would be rewritten by any future column touch,
    # whereas these two are the facts a user is shown.
    requested_at = Column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    responded_at = Column(DateTime, nullable=True)


class DbFollow(DBBaseModel):
    """
    One asymmetric follow (#276): a follower opted into someone else's public
    profile, unapproved and revocable by the follower alone.

    Deliberately not shaped like :class:`DbFriendship`. There is exactly one
    row per *direction* — ``follower_id``, ``followee_id`` — with no
    canonical ordering to enforce, because A following B and B following A
    are two unrelated facts, not two views of the same relationship. The
    ``UNIQUE`` pair just keeps a follower from accumulating duplicate rows
    for the same target.

    **Following grants no additional visibility.** Nothing in
    :mod:`app.services.visibility` reads this table — a follower resolves to
    the exact same tier ceiling as an anonymous visitor. If the followee's
    profile later drops out of the ``public`` tier, this row is not deleted;
    it simply stops admitting anything, same as any other stale grant that
    was never wired into the ceiling in the first place.

    Rows are meaningless without both parties, so deleting a user takes both
    their outgoing and incoming follows with them.
    """

    __tablename__ = 'follows'
    __table_args__ = (
        UniqueConstraint('follower_id', 'followee_id', name='uq_follows_pair'),
        CheckConstraint('follower_id != followee_id', name='ck_follows_not_self'),
    )

    follower_id = Column(
        Integer,
        ForeignKey('users.pk', ondelete='CASCADE'),
        nullable=False,
        index=True,
    )
    followee_id = Column(
        Integer,
        ForeignKey('users.pk', ondelete='CASCADE'),
        nullable=False,
        index=True,
    )
    # Explicit rather than created_at/updated_at: those are row bookkeeping
    # and could be rewritten by a future column touch, whereas this is the
    # one fact a user is shown ("following since").
    followed_at = Column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )


# Import sandbox models to ensure they are registered with the Base metadata
# pylint: disable=cyclic-import, wrong-import-position, unused-import
from app.db import models_sandbox  # noqa: F401
