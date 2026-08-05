# pylint: disable=missing-module-docstring, missing-function-docstring, protected-access, unused-variable
"""
Unit tests for app.migration scripts (enrich_tv, backfill_completed_at,
backfill_orion_timestamps, build_seed_fixtures).
Satisfies issue druthers-api#291.
"""

from datetime import datetime
import os
from unittest.mock import patch
import pytest

from app.db.models_sandbox import DbTVShow
from app.migration import (
    backfill_completed_at,
    backfill_orion_timestamps,
    build_seed_fixtures,
    enrich_tv,
)


def test_enrich_tv_run(test_db_session):
    show = DbTVShow(
        title='Pending Show',
        tvmaze=100,
        summary=None,
        premiered=None,
    )
    test_db_session.add(show)
    test_db_session.commit()

    with patch(
        'app.migration.enrich_tv.SessionLocal', return_value=test_db_session
    ), patch('app.migration.enrich_tv.get_tv_show_detail') as mock_detail, patch(
        'app.migration.enrich_tv.sync_episodes', return_value=5
    ), patch(
        'app.migration.enrich_tv.THROTTLE_SECONDS', 0
    ):
        mock_detail.return_value = {
            'summary': 'Show summary',
            'premiered': datetime(2020, 1, 1),
            'status': 'Ended',
            'genres': ['Drama'],
            'network': 'HBO',
            'rating': 8.5,
            'imdb': 'tt1234567',
        }
        # Prevent test session close
        test_db_session.close = lambda: None

        enrich_tv.run()

        test_db_session.refresh(show)
        assert show.summary == 'Show summary'


def test_backfill_completed_at_no_user(test_db_session):
    with patch.dict(os.environ, {'ORION_MYSQL_URL': 'sqlite:///:memory:'}), patch(
        'app.migration.backfill_completed_at.create_engine'
    ), patch(
        'app.migration.backfill_completed_at.SessionLocal', return_value=test_db_session
    ):
        test_db_session.close = lambda: None
        with pytest.raises(SystemExit):
            backfill_completed_at.run(dry_run=True, email='nonexistent@example.com')


def test_backfill_orion_timestamps_no_user(test_db_session):
    with patch.dict(os.environ, {'ORION_MYSQL_URL': 'sqlite:///:memory:'}), patch(
        'app.migration.backfill_orion_timestamps.create_engine'
    ), patch(
        'app.migration.backfill_orion_timestamps.SessionLocal',
        return_value=test_db_session,
    ):
        test_db_session.close = lambda: None
        with pytest.raises(SystemExit):
            backfill_orion_timestamps.run(dry_run=True, email='nonexistent@example.com')


def test_build_seed_fixtures_helpers(tmp_path):
    target_file = tmp_path / 'seed_test.json'
    data = [{'title': 'Test Item'}]
    build_seed_fixtures._write(target_file, data)

    assert target_file.exists()
    assert 'Test Item' in target_file.read_text()
