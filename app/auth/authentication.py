"""
This module creates tokens for users.
"""

import secrets
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, status
from fastapi.param_functions import Depends
from fastapi.security.oauth2 import OAuth2PasswordRequestForm
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm.session import Session

from app.auth import apple_identity, oauth2, refresh_tokens
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


class AppleAuthRequest(BaseModel):
    """
    Payload from Sign in with Apple.

    ``nonce`` is the RAW nonce the client generated; the token carries its
    SHA-256. ``full_name`` is here because Apple hands the name to the client
    once, on the very first authorization, and never again - if we do not take
    it now we cannot ask for it later.
    """

    identity_token: str
    nonce: Optional[str] = None
    full_name: Optional[str] = None


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
    if user.disabled_at is not None:
        # Correct credentials, but this account is not allowed to sign in.
        # The resolver in oauth2.py would reject the token on its very next
        # use anyway; rejecting here too means the client never sees a
        # "successful" sign-in that dies on the following request.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail='Account disabled'
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
    elif user.disabled_at is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail='Account disabled'
        )

    return _sign_in_response(user, db)


@router.post(
    '/refresh', response_model=OutToken, dependencies=[Depends(auth_rate_limit)]
)
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


@router.post(
    '/logout',
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(auth_rate_limit)],
)
def logout(request: InRefreshToken, db: Session = Depends(get_db)):
    """
    Sign out server-side: the refresh token and its family stop working.

    Deliberately 204 whether or not the token was recognised - sign-out must
    not depend on the client still holding a valid credential, and the status
    shouldn't reveal whether a guessed token existed.

    Signing out also ends any view-as session the owner of that refresh token
    is running (#341). Otherwise an admin could sign out believing they had
    closed everything down while a live impersonation token kept working
    until its own expiry.
    """
    owner_pk = refresh_tokens.owner_pk_for_token(db, request.refresh_token)
    refresh_tokens.revoke_refresh_token(db, request.refresh_token)
    if owner_pk is not None:
        db.query(models.DbImpersonationSession).filter(
            models.DbImpersonationSession.admin_user_pk == owner_pk,
            models.DbImpersonationSession.ended_at.is_(None),
        ).update({'ended_at': datetime.now(timezone.utc)}, synchronize_session=False)
        db.commit()


def _check_oauth_allowlist(email: str) -> None:
    """
    Enforce the invite-only allowlist (#183) against a resolved email.

    Applies to new AND existing accounts: during an invite-only phase the
    allowlist is the single source of truth for who may sign in, not just who
    may register.
    """
    allowlist = get_settings().oauth_allowlist_emails
    if allowlist is not None and email.lower() not in allowlist:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                'This app is invite-only right now. Your account '
                'isn’t on the access list - contact the administrator '
                'if you believe this is a mistake.'
            ),
        )


@router.post('/apple', response_model=OutToken, dependencies=[Depends(auth_rate_limit)])
def apple_login(request: AppleAuthRequest, db: Session = Depends(get_db)):
    """
    Sign in with an Apple identity token.

    Resolution order is deliberate, and it is the part with consequences:

    1. **By Apple subject.** ``sub`` is stable for this Apple ID against our
       developer team, and it survives the user turning off email relay or
       changing their Apple ID address.
    2. **By email, once, to link.** Someone who signed in with Google on the
       web and then with Apple on the phone is one person, and must land in
       one account. When Apple gives us a real, verified address that already
       belongs to an account, we stamp the Apple subject onto it rather than
       creating a second account. Splitting them here is cheap to prevent and
       expensive to merge later.
    3. **Otherwise create.** First sign-in with no matching account.

    Linking by email is deliberately NOT done for Apple private relay
    addresses. A relay is minted per app and will never equal the address on a
    Google account, so matching on one could only ever produce a false link.
    """
    settings = get_settings()
    client_ids = settings.apple_client_ids
    if not client_ids:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail='Apple sign-in is not configured',
        )

    try:
        identity = apple_identity.verify_identity_token(
            request.identity_token, client_ids, request.nonce
        )
    except apple_identity.AppleIdentityError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='Invalid Apple credential',
        ) from exc

    # Kept in Apple's casing for storage, lowercased only for comparison:
    # existing rows carry mixed-case addresses (`Dylan.Obrien@...`), so an
    # exact match would miss the very account we are trying to link to and
    # silently create a duplicate.
    email = (identity.email or '').strip() or None
    email_key = email.lower() if email else None

    user = (
        db.query(models.DbUser)
        .filter(models.DbUser.apple_sub == identity.subject)
        .first()
    )

    if user is None and email and identity.email_verified:
        if not identity.is_private_email:
            existing = (
                db.query(models.DbUser)
                .filter(func.lower(models.DbUser.email) == email_key)
                .first()
            )
            if existing is not None:
                _check_oauth_allowlist(existing.email)
                if existing.disabled_at is not None:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail='Account disabled',
                    )
                existing.apple_sub = identity.subject
                db.commit()
                db.refresh(existing)
                user = existing

    if user is None:
        if not email:
            # Apple omits the email claim in some re-authorization flows. With
            # no subject match and no address there is nothing to key a new
            # account on, and inventing a placeholder would create an account
            # the user can never reach from any other client.
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail='Apple credential carries no email address',
            )
        _check_oauth_allowlist(email)
        clash = (
            db.query(models.DbUser)
            .filter(func.lower(models.DbUser.email) == email_key)
            .first()
        )
        if clash is not None:
            # Only reachable when the address is a private relay that happens
            # to equal an existing account's address, since a non-relay match
            # would have linked above. Refusing beats inserting into a unique
            # index and turning a policy decision into a 500.
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail='An account already exists for this email address',
            )
        user = models.DbUser(
            email=email,
            display_name=(request.full_name or '').strip()[:30] or email,
            user_group='user',
            apple_sub=identity.subject,
            # Apple-authenticated users don't use a password; store an
            # unusable one, matching the Google flow.
            password=Hash.hash_password(secrets.token_hex(16)),
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    else:
        _check_oauth_allowlist(user.email)
        if user.disabled_at is not None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail='Account disabled'
            )

    return _sign_in_response(user, db)
