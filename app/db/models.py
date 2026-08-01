"""
This module defines the database models.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import backref, relationship

from app.db.database import Base


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

    # --- Visibility (#143): everything is private by default. A public
    # profile needs a handle (druthers.io/u/<handle>) plus at least one
    # category switched on; only ranked lists are ever exposed.
    handle = Column(String(length=30), unique=True, index=True, nullable=True)
    public_movies = Column(Boolean, nullable=True, default=False)
    public_tv = Column(Boolean, nullable=True, default=False)
    public_books = Column(Boolean, nullable=True, default=False)
    public_games = Column(Boolean, nullable=True, default=False)


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


# Import sandbox models to ensure they are registered with the Base metadata
# pylint: disable=cyclic-import, wrong-import-position, unused-import
from app.db import models_sandbox  # noqa: F401
