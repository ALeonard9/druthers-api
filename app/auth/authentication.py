"""
This module creates tokens for users.
"""

import secrets

from fastapi import APIRouter, HTTPException, status
from fastapi.param_functions import Depends
from fastapi.security.oauth2 import OAuth2PasswordRequestForm
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token
from pydantic import BaseModel
from sqlalchemy.orm.session import Session

from app.auth import oauth2, refresh_tokens
from app.services.rate_limit import auth_rate_limit, refresh_rate_limit
from app.config import get_settings
from app.db import models
from app.db.database import get_db
from app.db.hash import Hash
from app.schemas.model_schemas import InRefreshToken, OutToken

router = APIRouter(tags=['authentication'])


class GoogleAuthRequest(BaseModel):
    """Payload carrying the Google Identity Services ID token (credential)."""

    credential: str


def _token_response(user: models.DbUser, refresh_token: str) -> dict:
    """Build the standard token response for a user."""
    access_token = oauth2.create_access_token(data={'sub': user.id})
    return {
        'access_token': access_token,
        'refresh_token': refresh_token,
        'token_type': 'bearer',
        'expires_in': get_settings().access_token_expire_minutes * 60,
        'refresh_expires_in': get_settings().refresh_token_expire_days * 86400,
        'user_id': user.id,
        'user_group': user.user_group,
        'email': user.email,
        'time_zone': user.time_zone,
    }


def _sign_in_response(user: models.DbUser, db: Session) -> dict:
    """Token response for a fresh sign-in - starts a new rotation family."""
    return _token_response(user, refresh_tokens.issue_refresh_token(db, user))


@router.post('/token', response_model=OutToken, dependencies=[Depends(auth_rate_limit)])
def get_token(
    request: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)
):
    """
    Retrieves a JWT token if username (email) and password match

    Args:
        username: The email of the user
        password: The password of the user

    Returns:
        Access token, plus the refresh token that renews it
    """
    if get_settings().disable_password_login:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail='Password sign-in is disabled - use Google or an API key',
        )
    user = (
        # OAuth2PasswordRequestForm requires username instead of email
        db.query(models.DbUser)
        .filter(models.DbUser.email == request.username)
        .first()
    )
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail='Invalid credentials'
        )
    if not Hash.verify(user.password, request.password):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail='Invalid credentials'
        )

    return _sign_in_response(user, db)


@router.post(
    '/google', response_model=OutToken, dependencies=[Depends(auth_rate_limit)]
)
def google_login(request: GoogleAuthRequest, db: Session = Depends(get_db)):
    """
    Sign in with a Google Identity Services ID token.

    Verifies the token against the configured Google client id, then upserts the
    user (creating one on first sign-in) and returns a JWT - the same shape as
    the password flow.
    """
    settings = get_settings()
    client_ids = settings.google_client_ids
    if not client_ids:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail='Google sign-in is not configured',
        )
    try:
        # google-auth accepts a list here and requires the token's ``aud`` to
        # match one entry, so the web and native clients can both sign in
        # without loosening verification for either.
        info = google_id_token.verify_oauth2_token(
            request.credential,
            google_requests.Request(),
            client_ids,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='Invalid Google credential',
        ) from exc

    email = info.get('email')
    if not email or not info.get('email_verified', False):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='Google account email not verified',
        )

    allowlist = settings.oauth_allowlist_emails
    if allowlist is not None and email.lower() not in allowlist:
        # Applies to new AND existing accounts - during invite-only phases
        # (#183) the allowlist is the single source of truth for who may
        # sign in, not just who may register.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                'This app is invite-only right now. Your Google account '
                'isn’t on the access list - contact the administrator '
                'if you believe this is a mistake.'
            ),
        )

    user = db.query(models.DbUser).filter(models.DbUser.email == email).first()
    if user is None:
        user = models.DbUser(
            email=email,
            display_name=info.get('name') or email,
            user_group='user',
            # Google-authenticated users don't use a password; store an unusable one.
            password=Hash.hash_password(secrets.token_hex(16)),
        )
        db.add(user)
        db.commit()
        db.refresh(user)

    return _sign_in_response(user, db)


@router.post('/refresh', response_model=OutToken)
def refresh(request: InRefreshToken, db: Session = Depends(get_db)):
    """
    Trade a refresh token for a new access token, and a new refresh token.

    The presented token is spent: rotation means a stolen copy is only good
    until the legitimate client next refreshes, at which point the replay is
    detected and the whole session dies. Every failure is a flat 401 so the
    caller's only move is to send the user back to sign-in.
    """
    # Rate limit before rotating: throttling a rotation that already spent the
    # token would kill the very session the cap exists to protect.
    owner = refresh_tokens.peek_user(db, request.refresh_token)
    if owner is not None:
        refresh_rate_limit(owner)

    try:
        user, new_refresh_token = refresh_tokens.rotate_refresh_token(
            db, request.refresh_token
        )
    except refresh_tokens.RefreshTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='Invalid or expired refresh token',
            headers={'WWW-Authenticate': 'Bearer'},
        ) from exc

    return _token_response(user, new_refresh_token)


@router.post('/logout', status_code=status.HTTP_204_NO_CONTENT)
def logout(request: InRefreshToken, db: Session = Depends(get_db)):
    """
    Sign out server-side: the refresh token and its family stop working.

    Deliberately 204 whether or not the token was recognised - sign-out must
    not depend on the client still holding a valid credential, and the status
    shouldn't reveal whether a guessed token existed.
    """
    refresh_tokens.revoke_refresh_token(db, request.refresh_token)
