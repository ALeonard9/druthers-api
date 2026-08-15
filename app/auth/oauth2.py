"""
This module creates access tokens and verifys tokens.
"""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from fastapi import Depends, HTTPException, status
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


def generate_api_key() -> str:
    """Mint a new API key secret (returned to the user exactly once)."""
    return f'{API_KEY_PREFIX}{secrets.token_hex(24)}'


def hash_api_key(key: str) -> str:
    """SHA-256 of the full key - the only form ever stored."""
    return hashlib.sha256(key.encode()).hexdigest()


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
    row.last_used_at = datetime.now(timezone.utc)
    db.commit()
    return [row.user]


def _user_from_access_token(token: str, db: Session, credentials_exception):
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
    user = db_user.get_user(db, uuid)
    if user is None:
        raise credentials_exception
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


def get_current_user(
    token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)
):
    """
    This function verifies the current user
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail='Could not validate credentials',
        headers={'WWW-Authenticate': 'Bearer'},
    )
    if token.startswith(API_KEY_PREFIX):
        return _user_from_api_key(token, db, credentials_exception)
    return _user_from_access_token(token, db, credentials_exception)


def get_current_session_user(
    token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)
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
    return _user_from_access_token(token, db, credentials_exception)


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

    Unlike :func:`get_current_user`, this returns the ``DbUser`` itself (or
    ``None``) rather than a one-element list - there is nothing to unwrap.
    """
    if not token:
        return None
    try:
        return get_current_user(token, db)[0]
    except HTTPException as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='Could not validate credentials',
            headers={'WWW-Authenticate': 'Bearer'},
        ) from exc


def require_admin(current_user: list = Depends(get_current_user)) -> list:
    """
    Dependency that allows only admin users through.

    ``get_current_user`` returns a one-element list (``[DbUser]``); reuse it so
    the same object is available to the route.
    """
    if not current_user or current_user[0].user_group != 'admin':
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail='Admin privileges required',
        )
    return current_user
