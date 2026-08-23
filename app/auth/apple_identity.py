"""
Verification for Sign in with Apple identity tokens.

Apple's tokens are not Google's with a different logo. They are signed with
Apple's own keys, published at a different JWKS endpoint, and carry a
different issuer, so this is a parallel verification path rather than another
entry in the Google audience list (druthers-api#418).

The claim that matters is ``sub``. It is stable for a given Apple ID against a
given developer team and it is the only identifier we can trust across
sign-ins: ``email`` may be an Apple private relay address, and Apple only
guarantees the full name on the very first authorization.
"""

import hashlib
import secrets
from functools import lru_cache
from typing import List, NamedTuple, Optional

import jwt
from jwt import PyJWKClient

APPLE_ISSUER = 'https://appleid.apple.com'
APPLE_JWKS_URL = 'https://appleid.apple.com/auth/keys'

# Apple rotates its signing keys without notice, so the key set is fetched and
# cached rather than pinned. PyJWKClient refetches when it sees a `kid` it does
# not hold, which is what makes rotation a non-event; the lifespan just stops
# us hitting Apple on every sign-in.
_JWKS_CACHE_SECONDS = 900


class AppleIdentityError(Exception):
    """The token did not verify, or verified but is unusable."""


class AppleIdentity(NamedTuple):
    """The claims we actually act on, normalised."""

    subject: str
    email: Optional[str]
    is_private_email: bool
    email_verified: bool


@lru_cache(maxsize=1)
def _jwks_client() -> PyJWKClient:
    """One cached client per process; see _JWKS_CACHE_SECONDS."""
    return PyJWKClient(
        APPLE_JWKS_URL,
        cache_keys=True,
        cache_jwk_set=True,
        lifespan=_JWKS_CACHE_SECONDS,
    )


def _as_bool(value) -> bool:
    """
    Apple sends booleans as JSON booleans on some flows and as the strings
    'true'/'false' on others. Both mean the same thing and neither is a
    documented guarantee, so normalise instead of trusting one shape.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() == 'true'
    return False


def verify_identity_token(
    token: str,
    audiences: List[str],
    expected_nonce: Optional[str] = None,
) -> AppleIdentity:
    """
    Verify an Apple identity token and return the claims we act on.

    ``audiences`` is every client id we accept, matching how
    ``settings.google_client_ids`` works: PyJWT requires ``aud`` to match one
    entry, so widening the list adds issuing clients without weakening
    verification of any of them.

    ``expected_nonce`` is the RAW nonce the client generated. Apple echoes back
    whatever was put in the authorization request, and the convention is to put
    the SHA-256 of the raw nonce there, so that is what we compare against. A
    token carrying a nonce is only accepted when the caller can produce the raw
    value, which is what makes it replay protection rather than decoration.
    """
    if not audiences:
        raise AppleIdentityError('Apple sign-in is not configured')

    try:
        signing_key = _jwks_client().get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=['RS256'],
            audience=audiences,
            issuer=APPLE_ISSUER,
            options={'require': ['sub', 'aud', 'iss', 'exp']},
        )
    except (jwt.PyJWTError, jwt.exceptions.PyJWKClientError) as exc:
        raise AppleIdentityError('Invalid Apple credential') from exc

    subject = claims.get('sub')
    if not subject:
        # `require` above should have caught this; belt and braces, because an
        # empty subject would silently become an account nobody can sign back
        # into.
        raise AppleIdentityError('Apple credential has no subject')

    token_nonce = claims.get('nonce')
    if token_nonce is not None:
        if not expected_nonce:
            raise AppleIdentityError('Apple credential requires a nonce')
        hashed = hashlib.sha256(expected_nonce.encode('utf-8')).hexdigest()
        if not secrets.compare_digest(hashed, str(token_nonce)):
            raise AppleIdentityError('Apple credential nonce mismatch')

    return AppleIdentity(
        subject=subject,
        email=claims.get('email'),
        is_private_email=_as_bool(claims.get('is_private_email')),
        email_verified=_as_bool(claims.get('email_verified')),
    )
