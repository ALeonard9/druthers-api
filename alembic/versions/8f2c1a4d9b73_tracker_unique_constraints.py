"""tracker uniqueness and ranked-list indexes

Concurrent tracker writes could create more than one row for the same user
and catalog item because the existing composite indexes were not unique. Clean
up any duplicates before replacing those indexes with database constraints.

The four ranked trackers also get a partial index matching the hot ranked-list
query. Episode trackers have no ``on_rankings`` or ``rank`` columns, so they
only receive the identity constraint.

Revision ID: 8f2c1a4d9b73
Revises: e05c3cd4b323
Create Date: 2026-08-13 12:20:00.000000

"""

from typing import NamedTuple, Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '8f2c1a4d9b73'
down_revision: Union[str, Sequence[str], None] = 'e05c3cd4b323'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


class TrackerTable(NamedTuple):
    """Columns needed to enforce one tracker row per user and catalog item."""

    table_name: str
    fk_column: str
    ranked: bool = True


TRACKER_TABLES = (
    TrackerTable('user_movies', 'movie_id'),
    TrackerTable('user_tv_shows', 'tv_show_id'),
    TrackerTable('user_books', 'book_id'),
    TrackerTable('user_video_games', 'game_id'),
    TrackerTable('user_tv_episodes', 'episode_id', ranked=False),
)


def _old_index_name(tracker: TrackerTable) -> str:
    return f'ix_{tracker.table_name}_user_id_{tracker.fk_column}'


def _unique_constraint_name(tracker: TrackerTable) -> str:
    return f'uq_{tracker.table_name}_user_id_{tracker.fk_column}'


def _rank_index_name(tracker: TrackerTable) -> str:
    return f'ix_{tracker.table_name}_user_id_rank_on_rankings'


def _survivor_order(tracker: TrackerTable) -> str:
    if tracker.ranked:
        # Prefer any real list membership, then Rankings over Watchlist. A
        # lower rank and earlier creation time break the remaining ties.
        return """
            CASE WHEN on_rankings OR on_watchlist THEN 0 ELSE 1 END,
            CASE WHEN on_rankings THEN 0 ELSE 1 END,
            CASE WHEN rank IS NULL THEN 1 ELSE 0 END,
            rank,
            CASE WHEN created_at IS NULL THEN 1 ELSE 0 END,
            created_at,
            pk
        """

    # Episode trackers have no list/rank columns. Treat a watched or favorited
    # mark as active state so an empty duplicate cannot displace it.
    return """
        CASE WHEN watched <> 0 OR favorited THEN 0 ELSE 1 END,
        CASE WHEN created_at IS NULL THEN 1 ELSE 0 END,
        created_at,
        pk
    """


def _dedupe_tracker(tracker: TrackerTable) -> None:
    """Delete every duplicate except the deterministic preferred survivor."""
    # table_name/fk_column come from the literal TRACKER_TABLES tuple above,
    # never user input; this is a one-time migration cleanup, not a request path.
    # nosemgrep: avoid-sqlalchemy-text
    op.execute(sa.text(f"""
            WITH ordered_rows AS (
                SELECT pk,
                       ROW_NUMBER() OVER (
                           PARTITION BY user_id, {tracker.fk_column}
                           ORDER BY {_survivor_order(tracker)}
                       ) AS survivor_order
                  FROM {tracker.table_name}
            )
            DELETE FROM {tracker.table_name}
             WHERE pk IN (
                 SELECT pk
                   FROM ordered_rows
                  WHERE survivor_order > 1
             )
            """))


def _assert_no_duplicate_groups(tracker: TrackerTable) -> None:
    """Fail the migration before DDL if cleanup left any duplicate groups."""
    # table_name/fk_column come from the literal TRACKER_TABLES tuple above,
    # never user input; this is a one-time migration check, not a request path.
    # nosemgrep: avoid-sqlalchemy-text
    duplicate_groups = op.get_bind().execute(sa.text(f"""
            SELECT COUNT(*)
              FROM (
                  SELECT 1
                    FROM {tracker.table_name}
                   GROUP BY user_id, {tracker.fk_column}
                  HAVING COUNT(*) > 1
              ) AS duplicate_groups
            """)).scalar_one()
    if duplicate_groups:
        raise RuntimeError(
            f'{tracker.table_name} still has {duplicate_groups} duplicate '
            f'(user_id, {tracker.fk_column}) groups after dedupe'
        )


def upgrade() -> None:
    """Dedupe trackers, enforce identity, and index ranked-list reads."""
    for tracker in TRACKER_TABLES:
        _dedupe_tracker(tracker)
        op.drop_index(_old_index_name(tracker), table_name=tracker.table_name)
        _assert_no_duplicate_groups(tracker)
        op.create_unique_constraint(
            _unique_constraint_name(tracker),
            tracker.table_name,
            ['user_id', tracker.fk_column],
        )
        if tracker.ranked:
            op.create_index(
                _rank_index_name(tracker),
                tracker.table_name,
                ['user_id', 'rank'],
                postgresql_where=sa.text('on_rankings AND rank IS NOT NULL'),
            )


def downgrade() -> None:
    """Restore the non-unique composite indexes; deduped rows stay removed."""
    for tracker in reversed(TRACKER_TABLES):
        if tracker.ranked:
            op.drop_index(_rank_index_name(tracker), table_name=tracker.table_name)
        op.drop_constraint(
            _unique_constraint_name(tracker),
            tracker.table_name,
            type_='unique',
        )
        op.create_index(
            _old_index_name(tracker),
            tracker.table_name,
            ['user_id', tracker.fk_column],
        )
