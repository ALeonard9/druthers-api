"""tracker fk indexes

Postgres does not auto-index FK columns (#161). Every per-user query on the
five tracker tables was seq-scanning: invisible at 1 user / ~14k rows, not at
10x. Composite (user_id, <fk>) matches the real access pattern (a user's
tracker row for one catalog item) and covers user_id-only lookups too, since
it's the leading column.

Revision ID: bdf47c1723a4
Revises: a7c8d95e2f41
Create Date: 2026-07-20 07:59:28.807850

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'bdf47c1723a4'
down_revision: Union[str, Sequence[str], None] = 'a7c8d95e2f41'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (table, fk_column) - composite index is (user_id, fk_column)
TRACKER_FKS = (
    ('user_movies', 'movie_id'),
    ('user_tv_shows', 'tv_show_id'),
    ('user_tv_episodes', 'episode_id'),
    ('user_books', 'book_id'),
    ('user_video_games', 'game_id'),
)


def upgrade() -> None:
    """Upgrade schema."""
    for table_name, fk_column in TRACKER_FKS:
        op.create_index(
            f'ix_{table_name}_user_id_{fk_column}',
            table_name,
            ['user_id', fk_column],
        )


def downgrade() -> None:
    """Downgrade schema."""
    for table_name, fk_column in TRACKER_FKS:
        op.drop_index(f'ix_{table_name}_user_id_{fk_column}', table_name=table_name)
