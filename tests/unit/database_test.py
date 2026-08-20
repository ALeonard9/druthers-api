"""Tests for the app.db.database module."""

from importlib import reload
from unittest.mock import patch

from app.config import get_settings


def test_production_engine_connect_timeout():
    """
    Asserts the configured connect timeout is actually passed to the non-local engine.
    """
    settings = get_settings()

    with patch('sqlalchemy.create_engine') as mock_create_engine:
        with patch.object(settings, 'env', 'prod'):
            with patch.object(settings, 'db_connect_timeout', 7):
                with patch.object(settings, 'postgres_host', 'localhost'):
                    with patch.object(
                        settings,
                        'database_url',
                        'postgresql://user:pass@localhost:5432/db',
                    ):
                        import app.db.database  # pylint: disable=import-outside-toplevel

                        reload(app.db.database)

                        mock_create_engine.assert_called_once()
                        _, kwargs = mock_create_engine.call_args
                        assert 'connect_args' in kwargs
                        assert kwargs['connect_args'].get('connect_timeout') == 7
