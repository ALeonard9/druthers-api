'''
Unit tests for app.config.Settings - focused on the invite-only allowlist
parsing added for #183 (the rest of Settings is exercised indirectly by
every other test that calls get_settings()).
'''

import pytest
from pydantic import ValidationError

from app.config import Settings


def test_oauth_allowlist_emails_unset_is_none():
    """Unset (default) allowlist means the feature is a no-op."""
    settings = Settings(oauth_allowlist=None)
    assert settings.oauth_allowlist_emails is None


def test_oauth_allowlist_emails_blank_is_none():
    """A blank/whitespace-only value is treated the same as unset."""
    settings = Settings(oauth_allowlist='   ')
    assert settings.oauth_allowlist_emails is None


def test_oauth_allowlist_emails_parses_and_normalizes():
    """Entries are trimmed and lowercased so comparisons are case-insensitive."""
    settings = Settings(oauth_allowlist=' Adam@Example.com, second@example.com ,')
    assert settings.oauth_allowlist_emails == frozenset(
        {'adam@example.com', 'second@example.com'}
    )


def test_google_client_ids_empty_when_unset():
    """No client ids configured means Google sign-in is off."""
    settings = Settings(google_client_id=None, google_additional_client_ids=None)
    assert not settings.google_client_ids


def test_google_client_ids_falls_back_to_single_setting():
    """A deployment setting only GOOGLE_CLIENT_ID keeps working unchanged."""
    settings = Settings(google_client_id='web-123')
    assert settings.google_client_ids == ['web-123']


def test_google_client_ids_appends_additional_clients():
    """Native clients are added alongside the web one, primary first."""
    settings = Settings(
        google_client_id='web-123',
        google_additional_client_ids=' ios-456 , android-789 ,',
    )
    assert settings.google_client_ids == ['web-123', 'ios-456', 'android-789']


def test_google_client_ids_deduplicates():
    """A client id named in both settings is only sent once."""
    settings = Settings(
        google_client_id='web-123',
        google_additional_client_ids='web-123,ios-456',
    )
    assert settings.google_client_ids == ['web-123', 'ios-456']


def test_google_client_ids_without_primary():
    """Additional ids work even if the primary was never set."""
    settings = Settings(google_client_id=None, google_additional_client_ids='ios-456')
    assert settings.google_client_ids == ['ios-456']


def test_time_zone_rejects_unknown_iana_name():
    """A typo must fail at startup instead of quietly falling back to UTC."""
    with pytest.raises(ValidationError, match='Unknown IANA time zone'):
        Settings(time_zone='America/Not-A-Place')
