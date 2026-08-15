"""Tests for tracker dedupe, uniqueness, and ranked-list indexes."""

# pylint: disable=protected-access

import importlib.util
from pathlib import Path
from unittest.mock import Mock, call

import pytest
from sqlalchemy import UniqueConstraint, create_engine, text
from sqlalchemy.exc import IntegrityError
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.db.database import Base
from app.db.models_sandbox import (
    DbUserBook,
    DbUserMovie,
    DbUserTVEpisode,
    DbUserTVShow,
    DbUserVideoGame,
)

RANKED_TRACKERS = (
    ('user_movies', 'movie_id'),
    ('user_tv_shows', 'tv_show_id'),
    ('user_books', 'book_id'),
    ('user_video_games', 'game_id'),
)

TRACKER_MODELS = (
    (DbUserMovie, 'movie_id', True),
    (DbUserTVShow, 'tv_show_id', True),
    (DbUserBook, 'book_id', True),
    (DbUserVideoGame, 'game_id', True),
    (DbUserTVEpisode, 'episode_id', False),
)


def _migration_module():
    path = (
        Path(__file__).parents[2]
        / 'alembic/versions/8f2c1a4d9b73_tracker_unique_constraints.py'
    )
    spec = importlib.util.spec_from_file_location(
        'tracker_unique_constraints_migration', path
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _tracker(migration, table_name):
    return next(
        tracker
        for tracker in migration.TRACKER_TABLES
        if tracker.table_name == table_name
    )


def _create_ranked_tracker_table(connection, table_name, fk_column):
    connection.execute(text(f"""
            CREATE TABLE {table_name} (
                pk INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                {fk_column} INTEGER NOT NULL,
                on_watchlist BOOLEAN NOT NULL,
                on_rankings BOOLEAN NOT NULL,
                rank INTEGER,
                created_at TIMESTAMP
            )
            """))


@pytest.mark.parametrize(('table_name', 'fk_column'), RANKED_TRACKERS)
def test_ranked_tracker_dedupe_applies_every_survivor_priority(table_name, fk_column):
    """Ranked-table cleanup honors list, rank, date, and user boundaries."""
    migration = _migration_module()
    engine = create_engine('sqlite://')
    with engine.begin() as connection:
        _create_ranked_tracker_table(connection, table_name, fk_column)
        connection.execute(text(f"""
                INSERT INTO {table_name}
                    (pk, user_id, {fk_column}, on_watchlist, on_rankings,
                     rank, created_at)
                VALUES
                    (1, 1, 10, FALSE, TRUE, 4, '2020-01-01'),
                    (2, 1, 10, TRUE, FALSE, 1, '2019-01-01'),
                    (3, 1, 10, FALSE, TRUE, 1, '2021-01-01'),
                    (4, 1, 20, TRUE, FALSE, NULL, '2022-01-01'),
                    (5, 1, 20, FALSE, FALSE, NULL, '2020-01-01'),
                    (6, 1, 30, FALSE, FALSE, NULL, '2021-01-01'),
                    (7, 1, 30, FALSE, FALSE, NULL, '2020-01-01'),
                    (8, 2, 10, FALSE, TRUE, 2, '2018-01-01'),
                    (9, 1, 40, FALSE, TRUE, 2, '2022-01-01'),
                    (10, 1, 40, FALSE, TRUE, 2, '2020-01-01')
                """))
        migration.op = Operations(MigrationContext.configure(connection))

        migration._dedupe_tracker(_tracker(migration, table_name))

        surviving_pks = (
            connection.execute(text(f'SELECT pk FROM {table_name} ORDER BY pk'))
            .scalars()
            .all()
        )
        assert surviving_pks == [3, 4, 7, 8, 10]
        migration._assert_no_duplicate_groups(_tracker(migration, table_name))
    engine.dispose()


def test_episode_dedupe_prefers_active_state_then_earliest_creation():
    """Episode cleanup retains an active mark before an empty duplicate."""
    migration = _migration_module()
    tracker = _tracker(migration, 'user_tv_episodes')
    engine = create_engine('sqlite://')
    with engine.begin() as connection:
        connection.execute(text("""
                CREATE TABLE user_tv_episodes (
                    pk INTEGER PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    episode_id INTEGER NOT NULL,
                    watched INTEGER NOT NULL,
                    favorited BOOLEAN NOT NULL,
                    created_at TIMESTAMP
                )
                """))
        connection.execute(text("""
                INSERT INTO user_tv_episodes
                    (pk, user_id, episode_id, watched, favorited, created_at)
                VALUES
                    (1, 1, 10, 0, FALSE, '2019-01-01'),
                    (2, 1, 10, 1, FALSE, '2021-01-01'),
                    (3, 1, 10, 0, TRUE, '2022-01-01'),
                    (4, 1, 20, 0, FALSE, '2021-01-01'),
                    (5, 1, 20, 0, FALSE, '2020-01-01'),
                    (6, 2, 10, 0, TRUE, '2018-01-01')
                """))
        migration.op = Operations(MigrationContext.configure(connection))

        migration._dedupe_tracker(tracker)

        surviving_pks = (
            connection.execute(text('SELECT pk FROM user_tv_episodes ORDER BY pk'))
            .scalars()
            .all()
        )
        assert surviving_pks == [2, 5, 6]
        migration._assert_no_duplicate_groups(tracker)
    engine.dispose()


def test_duplicate_assertion_fails_loudly_before_constraint_creation():
    """Unexpected duplicate groups stop the migration with table context."""
    migration = _migration_module()
    tracker = _tracker(migration, 'user_movies')
    engine = create_engine('sqlite://')
    with engine.begin() as connection:
        _create_ranked_tracker_table(connection, 'user_movies', 'movie_id')
        connection.execute(text("""
                INSERT INTO user_movies
                    (pk, user_id, movie_id, on_watchlist, on_rankings, rank)
                VALUES
                    (1, 1, 10, FALSE, TRUE, 1),
                    (2, 1, 10, TRUE, FALSE, NULL)
                """))
        migration.op = Operations(MigrationContext.configure(connection))

        with pytest.raises(RuntimeError, match='still has 1 duplicate'):
            migration._assert_no_duplicate_groups(tracker)
    engine.dispose()


def test_upgrade_asserts_each_table_immediately_before_create_unique(monkeypatch):
    """Every tracker is checked directly before its unique DDL executes."""
    migration = _migration_module()
    actions = []
    operation_mock = Mock()
    operation_mock.drop_index.side_effect = lambda name, **kwargs: actions.append(
        ('drop_index', kwargs['table_name'])
    )
    operation_mock.create_unique_constraint.side_effect = (
        lambda name, table_name, columns: actions.append(('unique', table_name))
    )
    operation_mock.create_index.side_effect = (
        lambda name, table_name, columns, **kwargs: actions.append(
            ('rank_index', table_name)
        )
    )
    migration.op = operation_mock
    monkeypatch.setattr(
        migration,
        '_dedupe_tracker',
        lambda tracker: actions.append(('dedupe', tracker.table_name)),
    )
    monkeypatch.setattr(
        migration,
        '_assert_no_duplicate_groups',
        lambda tracker: actions.append(('assert', tracker.table_name)),
    )

    migration.upgrade()

    for tracker in migration.TRACKER_TABLES:
        assertion = actions.index(('assert', tracker.table_name))
        assert actions[assertion - 2] == ('dedupe', tracker.table_name)
        assert actions[assertion - 1] == ('drop_index', tracker.table_name)
        assert actions[assertion + 1] == ('unique', tracker.table_name)
    assert operation_mock.create_unique_constraint.call_args_list == [
        call(
            f'uq_{tracker.table_name}_user_id_{tracker.fk_column}',
            tracker.table_name,
            ['user_id', tracker.fk_column],
        )
        for tracker in migration.TRACKER_TABLES
    ]
    assert [
        called.args[1] for called in operation_mock.create_index.call_args_list
    ] == [table_name for table_name, _ in RANKED_TRACKERS]
    assert all(
        str(called.kwargs['postgresql_where']) == 'on_rankings AND rank IS NOT NULL'
        for called in operation_mock.create_index.call_args_list
    )


def test_downgrade_restores_original_non_unique_indexes():
    """Rollback removes new schema objects and restores the old indexes."""
    migration = _migration_module()
    operation_mock = Mock()
    migration.op = operation_mock

    migration.downgrade()

    reversed_trackers = list(reversed(migration.TRACKER_TABLES))
    assert operation_mock.drop_constraint.call_args_list == [
        call(
            f'uq_{tracker.table_name}_user_id_{tracker.fk_column}',
            tracker.table_name,
            type_='unique',
        )
        for tracker in reversed_trackers
    ]
    assert operation_mock.create_index.call_args_list == [
        call(
            f'ix_{tracker.table_name}_user_id_{tracker.fk_column}',
            tracker.table_name,
            ['user_id', tracker.fk_column],
        )
        for tracker in reversed_trackers
    ]
    assert operation_mock.drop_index.call_args_list == [
        call(
            f'ix_{tracker.table_name}_user_id_rank_on_rankings',
            table_name=tracker.table_name,
        )
        for tracker in reversed_trackers
        if tracker.ranked
    ]


@pytest.mark.parametrize(('model', 'fk_column', 'ranked'), TRACKER_MODELS)
def test_models_enforce_tracker_identity_and_declare_rank_index(
    model, fk_column, ranked
):
    """ORM metadata mirrors each new constraint and applicable rank index."""
    table = model.__table__
    unique_constraints = [
        constraint
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
        and {column.name for column in constraint.columns} == {'user_id', fk_column}
    ]
    assert [constraint.name for constraint in unique_constraints] == [
        f'uq_{table.name}_user_id_{fk_column}'
    ]

    rank_indexes = [
        index
        for index in table.indexes
        if index.name == f'ix_{table.name}_user_id_rank_on_rankings'
    ]
    assert len(rank_indexes) == int(ranked)
    if ranked:
        assert [column.name for column in rank_indexes[0].columns] == [
            'user_id',
            'rank',
        ]
        assert (
            str(rank_indexes[0].dialect_options['postgresql']['where'])
            == 'on_rankings AND rank IS NOT NULL'
        )


@pytest.mark.parametrize(('model', 'fk_column', 'ranked'), TRACKER_MODELS)
def test_model_unique_constraint_rejects_duplicate_tracker_rows(
    model, fk_column, ranked
):
    """Model-created schemas reject duplicate user/catalog tracker pairs."""
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    values = {'user_id': 1, fk_column: 10}
    if ranked:
        values.update(on_watchlist=False, on_rankings=True, rank=1)
    else:
        values.update(watched=1, favorited=False)

    with engine.begin() as connection:
        connection.execute(model.__table__.insert().values(**values))
        with pytest.raises(IntegrityError):
            connection.execute(model.__table__.insert().values(**values))
    engine.dispose()
