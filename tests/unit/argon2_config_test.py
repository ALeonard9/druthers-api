"""
Unit test verifying Argon2 hashing cost configuration (#285).

Ensures that reduced cost parameters apply strictly in the test environment,
and non-test/production environments preserve standard strong Argon2 defaults.
"""

from app.config import Settings


def test_argon2_params_in_test_env():
    """In test environment (env='test'), argon2_params returns low cost settings."""
    settings = Settings(env='test')
    assert settings.argon2_params == {
        'time_cost': 1,
        'memory_cost': 8,
        'parallelism': 1,
    }


def test_argon2_params_in_production_env():
    """In production/local environments, argon2_params defaults to empty dict."""
    settings = Settings(env='prod')
    assert not settings.argon2_params

    settings_local = Settings(env='local')
    assert not settings_local.argon2_params
