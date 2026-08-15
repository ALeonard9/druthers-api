"""
Issue, rotate, and revoke refresh tokens (#246).

The access token stays short-lived and stateless; this module owns the piece
that makes staying signed in safe - a long-lived opaque token that can be
killed individually, rotates every time it is spent, and takes its whole
family down with it if a spent one comes back.

Nothing here touches API keys (``drk_``), which authenticate directly and are
deliberately out of this flow.
"""

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import DbRefreshToken, DbUser
from app.log.logging_config import logger

# Distinct from the API key prefix so a refresh token pasted into an
# Authorization header fails as a malformed JWT instead of being probed
# against the API key table.
REFRESH_TOKEN_PREFIX = 'drr_'


class RefreshTokenError(Exception):
    """A presented refresh token is unknown, expired, revoked, or replayed."""


def generate_refresh_token() -> str:
    """Mint the plaintext secret - returned to the caller exactly once."""
    return f'{REFRESH_TOKEN_PREFIX}{secrets.token_urlsafe(32)}'


def hash_refresh_token(token: str) -> str:
    """SHA-256 of the full token - the only form ever stored."""
    return hashlib.sha256(token.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    """
    Read a stored timestamp back as UTC-aware.

    The columns are naive ``DateTime`` and SQLite hands back naive values, so
    comparing a stored expiry against an aware ``now()`` would raise. Postgres
    behaves the same way for a bare ``timestamp``.
    """
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def issue_refresh_token(
    db: Session, user: DbUser, family_id: Optional[str] = None
) -> str:
    """
    Store a new refresh token for ``user`` and return the plaintext.

    Pass ``family_id`` to continue an existing session's rotation chain; omit
    it for a fresh sign-in, which starts a new family.
    """
    settings = get_settings()
    token = generate_refresh_token()
    row = DbRefreshToken(
        user_id=user.pk,
        token_hash=hash_refresh_token(token),
        family_id=family_id or str(uuid.uuid4()),
        expires_at=_now() + timedelta(days=settings.refresh_token_expire_days),
    )
    db.add(row)
    _purge_expired(db, user)
    db.commit()
    return token


def _purge_expired(db: Session, user: DbUser) -> None:
    """
    Drop this user's already-expired rows so the table stays bounded.

    Safe for replay detection: an expired token is rejected whether or not the
    row survives, so forgetting it costs nothing.
    """
    db.query(DbRefreshToken).filter(
        DbRefreshToken.user_id == user.pk,
        DbRefreshToken.expires_at < _now(),
    ).delete(synchronize_session=False)


def revoke_family(db: Session, family_id: str) -> None:
    """Revoke every unrevoked token in a rotation chain."""
    db.query(DbRefreshToken).filter(
        DbRefreshToken.family_id == family_id,
        DbRefreshToken.revoked_at.is_(None),
    ).update({'revoked_at': _now()}, synchronize_session=False)


def revoke_refresh_token(db: Session, token: str) -> bool:
    """
    Revoke a token and the rest of its family - this is what sign-out calls.

    The family goes too, so signing out on a device can't be undone by a
    token that happened to be minted earlier in the same chain. Returns
    whether the token was recognised; an unknown token is not an error, since
    sign-out has to succeed regardless.
    """
    row = (
        db.query(DbRefreshToken)
        .filter(DbRefreshToken.token_hash == hash_refresh_token(token))
        .first()
    )
    if row is None:
        return False
    revoke_family(db, row.family_id)
    db.commit()
    return True


def _family_is_terminated(db: Session, family_id: str) -> bool:
    """
    True once a family has been killed outright rather than rotated onward.

    Sign-out and replay-detection both revoke tokens that were never spent,
    so a revoked row with no ``used_at`` is the signature of a session that
    was ended deliberately. Rotation never leaves one behind.
    """
    return (
        db.query(DbRefreshToken)
        .filter(
            DbRefreshToken.family_id == family_id,
            DbRefreshToken.revoked_at.isnot(None),
            DbRefreshToken.used_at.is_(None),
        )
        .first()
        is not None
    )


def _within_reuse_leeway(db: Session, row: DbRefreshToken) -> bool:
    """
    True when a revoked token was spent by rotation moments ago.

    Two guards, not one. ``used_at`` rules out a token revoked at sign-out;
    the family check rules out re-presenting an *earlier* token from a family
    that has since been signed out, which would otherwise resurrect the
    session inside the grace window.
    """
    if row.used_at is None:
        return False
    leeway = timedelta(seconds=get_settings().refresh_token_reuse_leeway_seconds)
    if _now() - _as_utc(row.used_at) > leeway:
        return False
    return not _family_is_terminated(db, row.family_id)


def peek_user(db: Session, token: str) -> Optional[DbUser]:
    """
    Owner of a refresh token without spending it, or ``None`` if unknown.

    Only for checks that must happen *before* rotation - rate limiting a
    rotation that already consumed the token would destroy the session it was
    meant to protect. Says nothing about whether the token is still valid.
    """
    row = (
        db.query(DbRefreshToken)
        .filter(DbRefreshToken.token_hash == hash_refresh_token(token))
        .first()
    )
    return row.user if row else None


def rotate_refresh_token(db: Session, token: str) -> Tuple[DbUser, str]:
    """
    Spend a refresh token and return ``(user, new_plaintext_token)``.

    Raises ``RefreshTokenError`` for anything the caller should answer with a
    401: unknown, expired, or already-spent. A replayed token additionally
    revokes its family, on the assumption that a token used twice is a token
    someone else also has.
    """
    row = (
        db.query(DbRefreshToken)
        .filter(DbRefreshToken.token_hash == hash_refresh_token(token))
        .first()
    )
    if row is None:
        raise RefreshTokenError('Unknown refresh token')

    if row.revoked_at is not None:
        if _within_reuse_leeway(db, row):
            # Two of the client's requests raced past the same expiry. Hand
            # out another token in the family rather than reading a race as
            # theft - the alternative signs people out at random.
            logger.info(
                'Concurrent refresh for user_id=%s family=%s within the reuse '
                'window; issuing an additional token',
                row.user_id,
                row.family_id,
            )
            return row.user, issue_refresh_token(db, row.user, family_id=row.family_id)

        # Outside the window this is a genuine replay of a spent token, or a
        # token revoked at sign-out. Either way the chain is no longer
        # trustworthy and everything in it dies.
        logger.warning(
            'Refresh token replay detected for user_id=%s family=%s; '
            'revoking the family',
            row.user_id,
            row.family_id,
        )
        revoke_family(db, row.family_id)
        db.commit()
        raise RefreshTokenError('Refresh token already used')

    if _as_utc(row.expires_at) <= _now():
        raise RefreshTokenError('Refresh token expired')

    user = row.user
    if user is None:
        raise RefreshTokenError('Refresh token has no owner')

    now = _now()
    row.revoked_at = now
    row.used_at = now
    # Same family, fresh expiry: an active session slides forward instead of
    # hitting a hard wall mid-use.
    return user, issue_refresh_token(db, user, family_id=row.family_id)
