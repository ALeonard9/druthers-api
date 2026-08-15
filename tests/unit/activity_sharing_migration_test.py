"""The activity-sharing migration adds a default-on, reversible switch."""

import importlib.util
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from alembic.migration import MigrationContext
from alembic.operations import Operations


def _migration_module():
    path = (
        Path(__file__).parents[2]
        / 'alembic/versions/e2c7a94f1b30_activity_sharing_opt_out.py'
    )
    spec = importlib.util.spec_from_file_location('activity_sharing_migration', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_upgrade_defaults_existing_users_in_and_downgrade_is_reversible():
    """Upgrade existing data safely, then restore the original schema."""
    migration = _migration_module()
    engine = create_engine('sqlite://')
    with engine.begin() as connection:
        connection.execute(text('CREATE TABLE users (pk INTEGER PRIMARY KEY)'))
        connection.execute(text('INSERT INTO users (pk) VALUES (1)'))
        migration.op = Operations(MigrationContext.configure(connection))

        migration.upgrade()
        columns = {
            column['name']: column
            for column in inspect(connection).get_columns('users')
        }
        assert columns['share_activity']['nullable'] is False
        assert (
            connection.execute(
                text('SELECT share_activity FROM users WHERE pk = 1')
            ).scalar_one()
            == 1
        )

        migration.downgrade()
        assert {
            column['name'] for column in inspect(connection).get_columns('users')
        } == {'pk'}
    engine.dispose()
