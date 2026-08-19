"""
This module creates access tokens and verifys tokens.
"""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import db_user
from app.db.database import get_db
from app.log.logging_config import logger

settings = get_settings()

oauth2_scheme = OAuth2PasswordBearer(tokenUrl='v1/auth/token')
# Same scheme, but a missing Authorization header yields None instead of a
# 401 - the endpoints that serve anonymous and signed-in callers alike
# (#277's public profile) need to know *whether* somebody is signed in.
optional_oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl='v1/auth/token', auto_error=False
)


def _resolve_secret_key() -> str:
    """
    Resolve the JWT signing secret.

    A fixed ``JWT_SECRET_KEY`` is required in deployed environments (dev/prod)
    so tokens survive restarts and are shared across workers. Local/CI fall
    back to a random per-process key with a warning.
    """
    # openssl rand -hex 32 to generate a new secret key
    if settings.jwt_secret_key:
        return settings.jwt_secret_key
    if settings.env in ('dev', 'qa', 'prod', 'gs'):
        raise RuntimeError(
            f'JWT_SECRET_KEY must be set in the {settings.env} environment'
        )
    logger.warning(
        'JWT_SECRET_KEY not set; using a randomly generated key for this '
        'process. Tokens will not persist across restarts or be shared '
        'across workers.'
    )
    return secrets.token_hex(32)


SECRET_KEY = _resolve_secret_key()
ALGORITHM = 'HS256'
ACCESS_TOKEN_EXPIRE_MINUTES = settings.access_token_expire_minutes

# API keys ride the same Authorization: Bearer header as JWTs; the prefix is
# how we tell them apart (and lets secret scanners recognize leaked keys).
API_KEY_PREFIX = 'drk_'

# Impersonation access tokens carry this in ``typ`` so an ordinary session
# token can never be mistaken for one, and vice versa (#341).
IMPERSONATION_TOKEN_TYPE = 'impersonation'
# Absolute, and deliberately short. There is no refresh path: expiry is the
# escape hatch for a session somebody walked away from.
IMPERSONATION_TOKEN_MINUTES = 15
# Everything else is refused while impersonating.
_SAFE_METHODS = frozenset({'GET', 'HEAD', 'OPTIONS'})


def generate_api_key() -> str:
    """Mint a new API key secret (returned to the user exactly once)."""
    return f'{API_KEY_PREFIX}{secrets.token_hex(24)}'


def hash_api_key(key: str) -> str:
    """SHA-256 of the full key - the only form ever stored."""
    return hashlib.sha256(key.encode()).hexdigest()


def _disabled_exception() -> HTTPException:
    """
    Raised by both credential resolvers below for a disabled account.

    Distinct from ``credentials_exception`` (401) on purpose: a 401 tells a
    web BFF the token expired and sends it into a refresh, which also fails
    for a disabled user and leaves the client looping. 403 says plainly that
    the credential itself was fine and the account is the reason - the only
    answer that lets a client stop retrying and show the right message.

    This is the enforcement point, not ``/auth/token``/``/auth/google``/
    ``/auth/refresh`` alone: access JWTs are stateless with no ``jti``, so a
    live one keeps resolving successfully for its full TTL unless the
    resolver itself checks on every use. Refresh tokens and API keys are
    also revoked/deleted outright when an account is disabled (see
    ``app.router.v1.router_admin.disable_user``), so this check is a second,
    belt-and-suspenders layer for anything minted in the window before that
    ran, not the only thing stopping a disabled account.
    """
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail='Account disabled',
    )


def _user_from_api_key(token: str, db: Session, credentials_exception):
    """
    Resolve a ``drk_`` bearer token to its owner, or raise.

    Returns the same one-element-list shape as the JWT path so routes can't
    tell the difference.
    """
    # Local import: models imports nothing from here, but keeping it out of
    # module scope avoids widening the auth module's import surface.
    from app.db.models import DbApiKey  # pylint: disable=import-outside-toplevel

    row = db.query(DbApiKey).filter(DbApiKey.key_hash == hash_api_key(token)).first()
    if row is None:
        raise credentials_exception
    if row.user.disabled_at is not None:
        raise _disabled_exception()
    row.last_used_at = datetime.now(timezone.utc)
    db.commit()
    return [row.user]


def _impersonation_exception(detail: str) -> HTTPException:
    """Raised when an impersonation credential is presented but not honoured."""
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


def _resolve_impersonation(payload: dict, db: Session, request, credentials_exception):
    """
    Swap the resolved identity to the impersonation target, or raise.

    Called from :func:`_user_from_access_token` rather than from
    :func:`get_current_user`, and that placement is the whole security story.
    ``get_current_session_user`` is a second, independent decode path into
    this same function; swapping one level up would leave it resolving an
    impersonation token's ``sub`` to the target with no impersonation context
    attached, which reaches the self-delete branch of
    ``DELETE /v1/users/{uuid}``. A session documented as read-only would
    delete the account. Swapping here covers both callers, and sitting below
    the ``drk_`` branch in :func:`get_current_user` means no API key can ever
    reach impersonation.
    """
    # Local import keeps the auth module's import surface narrow, matching
    # the pattern used for DbApiKey above.
    from app.db.models import (  # pylint: disable=import-outside-toplevel
        DbImpersonationSession,
    )

    session_id = payload.get('sid')
    admin_id = payload.get('act')
    if not session_id or not admin_id:
        raise credentials_exception

    row = (
        db.query(DbImpersonationSession)
        .filter(DbImpersonationSession.id == session_id)
        .first()
    )
    now = datetime.now(timezone.utc)
    if row is None or row.ended_at is not None:
        raise _impersonation_exception('Impersonation session has ended')
    if (
        row.expires_at is not None
        and row.expires_at.replace(tzinfo=timezone.utc) <= now
    ):
        raise _impersonation_exception('Impersonation session has expired')

    # Re-check the acting admin on every request, not just at mint: they can
    # be demoted, disabled or deleted mid-session, and the session must die
    # with their privilege rather than outliving it.
    admin = row.admin
    if admin is None or admin.user_group != 'admin' or admin.disabled_at is not None:
        raise _impersonation_exception('Acting admin is no longer an administrator')

    target = row.target
    if target is None:
        raise credentials_exception
    # Also re-checked per request: a target promoted to admin mid-session must
    # stop being impersonable immediately.
    if target.user_group == 'admin':
        raise _impersonation_exception('An admin cannot be impersonated')

    # Read-only, with no override path by design (#341 was amended on
    # 2026-08-18 to cut "act on their behalf"). Enforced here, at the one
    # point every authenticated route funnels through, so a route cannot opt
    # out by forgetting a dependency. Deliberately not middleware: middleware
    # runs before dependency resolution and would have to decode the token
    # itself, creating exactly the second decode path this function exists to
    # avoid.
    if request is not None and request.method not in _SAFE_METHODS:
        raise _impersonation_exception(
            'This view-as session is read-only. End it to act as yourself.'
        )

    return [target]


def _user_from_access_token(
    token: str, db: Session, credentials_exception, request=None
):
    """Resolve a signed, expiring access JWT to its user."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        uuid: str = payload.get('sub')
        if uuid is None:
            raise credentials_exception
    except jwt.ExpiredSignatureError as exc:
        raise credentials_exception from exc
    except jwt.InvalidTokenError as exc:
        raise credentials_exception from exc
    if payload.get('typ') == IMPERSONATION_TOKEN_TYPE:
        return _resolve_impersonation(payload, db, request, credentials_exception)
    user = db_user.get_user(db, uuid)
    if user is None:
        raise credentials_exception
    if user[0].disabled_at is not None:
        raise _disabled_exception()
    return user


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    """
    This function creates an access token
    """
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(
            minutes=get_settings().access_token_expire_minutes
        )
    to_encode.update({'exp': expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def create_impersonation_token(target_id: str, admin_id: str, session_id: str):
    """
    Mint the credential for one view-as session, and only that.

    Deliberately not built on the sign-in helpers in
    :mod:`app.auth.authentication`: both of those mint a refresh token, and an
    impersonation session must never be refreshable. Expiry has to be a hard
    stop, or a diagnostic tool becomes a permanent alternate login.
    """
    expires = datetime.now(timezone.utc) + timedelta(
        minutes=IMPERSONATION_TOKEN_MINUTES
    )
    payload = {
        'sub': target_id,
        'act': admin_id,
        'typ': IMPERSONATION_TOKEN_TYPE,
        'sid': session_id,
        'exp': expires,
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM), expires


def is_impersonation_token(token: str) -> bool:
    """
    Whether a bearer token is an impersonation credential.

    Used to refuse nesting: an impersonated caller must not be able to mint a
    further session. ``require_admin`` already refuses them (the swapped
    identity is not an admin), so this is defence in depth for the case where
    an admin target somehow slipped through.
    """
    if not token or token.startswith(API_KEY_PREFIX):
        return False
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.InvalidTokenError:
        return False
    return payload.get('typ') == IMPERSONATION_TOKEN_TYPE


def get_current_user(
    request: Request = None,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
):
    """
    This function verifies the current user

    ``request`` is injected so the impersonation read-only rule can see the
    method at the single point every authenticated route already funnels
    through. It defaults to ``None`` because
    :func:`get_optional_current_user` calls this directly rather than through
    dependency injection.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail='Could not validate credentials',
        headers={'WWW-Authenticate': 'Bearer'},
    )
    if token.startswith(API_KEY_PREFIX):
        # API keys share this header with JWTs, so an API key must never be
        # able to carry impersonation claims. It cannot: this branch never
        # reads the token's payload at all.
        return _user_from_api_key(token, db, credentials_exception)
    return _user_from_access_token(token, db, credentials_exception, request)


def get_current_session_user(
    request: Request = None,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
):
    """
    Authenticate only an expiring interactive-session access token.

    API keys are intentionally not considered here. Destructive account
    deletion uses this dependency so a long-lived MCP or script credential
    cannot delete its owner, while the rest of the API can continue accepting
    both supported bearer credential types through :func:`get_current_user`.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail='Could not validate credentials',
        headers={'WWW-Authenticate': 'Bearer'},
    )
    return _user_from_access_token(token, db, credentials_exception, request)


def get_optional_current_user(
    token: Optional[str] = Depends(optional_oauth2_scheme),
    db: Session = Depends(get_db),
):
    """
    The current user when the caller is signed in, ``None`` when they are not.

    For endpoints that serve everybody but serve signed-in callers more
    (#277). Two deliberate choices:

    *Absent is anonymous; invalid is an error.* No bearer credentials at all -
    no header, another scheme, or an empty ``Bearer`` - means an anonymous
    viewer, because there is nothing there to reject. Credentials present but
    bad - expired,
    forged, revoked API key, or a token for a user who no longer exists - are
    a 401 with the usual ``WWW-Authenticate`` challenge, never a silent
    downgrade to the anonymous view. Downgrading would show a friend the
    stranger's version of a profile the moment their token expired, which
    reads as "they unfriended me" rather than "sign in again", and would let a
    client ship a broken token forever without noticing.

    *Every credential failure answers the same way.* The 404 lookup inside
    :func:`get_current_user` becomes the same 401 as everything else, so an
    expired token and a token for a deleted account are indistinguishable.
    A disabled account is the one deliberate exception: it stays a 403
    (see :func:`_disabled_exception`) rather than folding into the generic
    401, for the same reason it does everywhere else - a 401 here would send
    a disabled user's client into a refresh loop instead of telling it why.

    Unlike :func:`get_current_user`, this returns the ``DbUser`` itself (or
    ``None``) rather than a one-element list - there is nothing to unwrap.
    """
    if not token:
        return None
    try:
        # Keyword arguments, not positional: ``get_current_user`` gained a
        # leading ``request`` parameter for the impersonation read-only rule,
        # and this is the only direct (non-DI) call to it in the codebase.
        # Passing no request is correct here - this dependency serves reads.
        return get_current_user(token=token, db=db)[0]
    except HTTPException as exc:
        if exc.status_code == status.HTTP_403_FORBIDDEN:
            raise
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='Could not validate credentials',
            headers={'WWW-Authenticate': 'Bearer'},
        ) from exc


def is_admin(current_user: list) -> bool:
    """
    Whether the resolved caller belongs to the admin group.

    The one predicate :func:`require_admin` and any mixed admin-or-self
    route (``app.router.v1.user``) both test against, so "is this user an
    admin" has a single definition rather than the same string comparison
    copied at each call site.
    """
    return bool(current_user) and current_user[0].user_group == 'admin'


def require_admin(current_user: list = Depends(get_current_user)) -> list:
    """
    Dependency that allows only admin users through.

    ``get_current_user`` returns a one-element list (``[DbUser]``); reuse it so
    the same object is available to the route.
    """
    if not is_admin(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail='Admin privileges required',
        )
    return current_user
