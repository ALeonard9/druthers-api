"""
Tests for Apple identity token verification.

These sign real tokens with a real RSA key and verify them through the real
code path, stubbing only the network fetch of Apple's key set. Mocking
``jwt.decode`` instead would assert that we call a library, not that a forged
token is actually rejected, which is the only thing worth proving here.
"""

import hashlib
import time
from unittest.mock import patch

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.auth import apple_identity
from app.auth.apple_identity import (
    AppleIdentityError,
    verify_identity_token,
)

AUDIENCE = 'io.druthers.ios'


@pytest.fixture(name='signing_key')
def signing_key_fixture():
    """One 2048-bit key for the whole module; generation is the slow part."""
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(name='other_key')
def other_key_fixture():
    """A key Apple never published, for the forged-signature case."""
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _make_token(key, **overrides):
    """Mint an Apple-shaped identity token."""
    now = int(time.time())
    claims = {
        'iss': 'https://appleid.apple.com',
        'aud': AUDIENCE,
        'sub': '001234.abcdef.5678',
        'iat': now,
        'exp': now + 600,
        'email': 'person@example.com',
        'email_verified': 'true',
        'is_private_email': 'false',
    }
    claims.update(overrides)
    for empty in [k for k, v in claims.items() if v is None]:
        del claims[empty]
    return jwt.encode(claims, key, algorithm='RS256')


def _verify(token, key, **kwargs):
    """Run verification with Apple's key set stubbed to ``key``."""

    class _StubKey:
        def __init__(self, public_key):
            self.key = public_key

    with patch.object(apple_identity, '_jwks_client') as jwks:
        jwks.return_value.get_signing_key_from_jwt.return_value = _StubKey(
            key.public_key()
        )
        return verify_identity_token(token, [AUDIENCE], **kwargs)


def test_accepts_a_valid_token(signing_key):
    """The happy path, with the claims normalised the way the route reads them."""
    identity = _verify(_make_token(signing_key), signing_key)

    assert identity.subject == '001234.abcdef.5678'
    assert identity.email == 'person@example.com'
    assert identity.email_verified is True
    assert identity.is_private_email is False


def test_rejects_a_token_signed_by_the_wrong_key(signing_key, other_key):
    """A forged token verified against Apple's real key set must not pass."""
    forged = _make_token(other_key)

    with pytest.raises(AppleIdentityError):
        _verify(forged, signing_key)


def test_rejects_the_wrong_audience(signing_key):
    """A token minted for a different app must not sign anyone in here."""
    token = _make_token(signing_key, aud='com.someone.else')

    with pytest.raises(AppleIdentityError):
        _verify(token, signing_key)


def test_rejects_the_wrong_issuer(signing_key):
    """Guards against a token minted by another provider with our audience."""
    token = _make_token(signing_key, iss='https://accounts.google.com')

    with pytest.raises(AppleIdentityError):
        _verify(token, signing_key)


def test_rejects_an_expired_token(signing_key):
    """Expiry is enforced by us, not left to the client to respect."""
    now = int(time.time())
    token = _make_token(signing_key, iat=now - 7200, exp=now - 3600)

    with pytest.raises(AppleIdentityError):
        _verify(token, signing_key)


def test_rejects_a_token_with_no_configured_audience(signing_key):
    """An unconfigured environment refuses rather than accepting any audience."""
    with pytest.raises(AppleIdentityError):
        verify_identity_token(_make_token(signing_key), [])


def test_accepts_a_matching_nonce(signing_key):
    """The token carries SHA-256 of the raw nonce; the caller supplies the raw."""
    raw = 'a-random-nonce'
    hashed = hashlib.sha256(raw.encode()).hexdigest()
    token = _make_token(signing_key, nonce=hashed)

    identity = _verify(token, signing_key, expected_nonce=raw)

    assert identity.subject == '001234.abcdef.5678'


def test_rejects_a_mismatched_nonce(signing_key):
    """A replayed token carries someone else's nonce."""
    token = _make_token(
        signing_key, nonce=hashlib.sha256(b'the-real-nonce').hexdigest()
    )

    with pytest.raises(AppleIdentityError):
        _verify(token, signing_key, expected_nonce='a-different-nonce')


def test_rejects_a_nonce_token_when_the_caller_supplies_none(signing_key):
    """
    Otherwise nonce checking is opt-out by omission: a replayer would just
    drop the raw nonce from the request and sail through.
    """
    token = _make_token(
        signing_key, nonce=hashlib.sha256(b'the-real-nonce').hexdigest()
    )

    with pytest.raises(AppleIdentityError):
        _verify(token, signing_key)


def test_normalises_apple_string_booleans(signing_key):
    """Apple sends these as JSON booleans on some flows and strings on others."""
    token = _make_token(signing_key, email_verified=True, is_private_email=True)

    identity = _verify(token, signing_key)

    assert identity.email_verified is True
    assert identity.is_private_email is True


def test_treats_a_missing_email_as_absent(signing_key):
    """Apple omits the claim on some re-authorizations; that is not an error here."""
    token = _make_token(signing_key, email=None)

    identity = _verify(token, signing_key)

    assert identity.email is None
