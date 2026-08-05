"""Record the first profile that inspired a tracker item.

Revision ID: f6a31c8d9e42
Revises: 3c45fe305813
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'f6a31c8d9e42'
down_revision: Union[str, Sequence[str], None] = '3c45fe305813'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = ('user_movies', 'user_tv_shows', 'user_books', 'user_video_games')


def upgrade() -> None:
    for table in TABLES:
        op.add_column(table, sa.Column('source_user_id', sa.Integer(), nullable=True))
        op.create_foreign_key(
            f'fk_{table}_source_user_id_users',
            table,
            'users',
            ['source_user_id'],
            ['pk'],
            ondelete='SET NULL',
        )
        op.create_index(f'ix_{table}_source_user_id', table, ['source_user_id'])


def downgrade() -> None:
    for table in reversed(TABLES):
        op.drop_index(f'ix_{table}_source_user_id', table_name=table)
        op.drop_constraint(
            f'fk_{table}_source_user_id_users', table, type_='foreignkey'
        )
        op.drop_column(table, 'source_user_id')
