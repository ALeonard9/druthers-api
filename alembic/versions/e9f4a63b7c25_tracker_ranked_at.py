"""tracker ranked_at

Adds ranked_at to the five per-user tracker tables (#141): the timestamp of
the current rank assignment, so the Activity feed stops re-dating rankings
whenever an unrelated tracker field (notes, flags) bumps updated_at.
Backfills existing placed ranks from updated_at - the best signal available.

Revision ID: e9f4a63b7c25
Revises: d8e3f52a6b14
Create Date: 2026-07-18 13:35:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'e9f4a63b7c25'
down_revision: Union[str, Sequence[str], None] = 'd8e3f52a6b14'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TRACKER_TABLES = (
    'user_movies',
    'user_tv_shows',
    'user_books',
    'user_video_games',
    'user_countries',
)


def upgrade() -> None:
    """Upgrade schema."""
    for table_name in TRACKER_TABLES:
        with op.batch_alter_table(table_name, schema=None) as batch_op:
            batch_op.add_column(sa.Column('ranked_at', sa.DateTime(), nullable=True))
        tracker = sa.table(
            table_name,
            sa.column('rank', sa.Integer),
            sa.column('ranked_at', sa.DateTime),
            sa.column('updated_at', sa.DateTime),
        )
        op.execute(
            tracker.update()
            .where(tracker.c.rank.isnot(None))
            .values(ranked_at=tracker.c.updated_at)
        )


def downgrade() -> None:
    """Downgrade schema."""
    for table_name in TRACKER_TABLES:
        with op.batch_alter_table(table_name, schema=None) as batch_op:
            batch_op.drop_column('ranked_at')
