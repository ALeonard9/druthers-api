"""seed data marker on tracker tables

Revision ID: 6b17f4396788
Revises: a1c4f80b6e37
Create Date: 2026-07-25 23:19:40.404450

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '6b17f4396788'
down_revision: Union[str, Sequence[str], None] = 'a1c4f80b6e37'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TRACKER_TABLES = ('user_movies', 'user_tv_shows', 'user_video_games', 'user_books')


def upgrade() -> None:
    """
    Add ``is_seed_data`` to the four tracker tables.

    Unlike the old Faker seeder, ``seed_dev`` (#228) now writes *real* catalog
    rows keyed on real external ids, so a reserved-id-range check can no
    longer tell a seeded row apart from a real one. What's actually
    synthetic is the tracker row -- which list it's on, its rank, its
    completion date -- so that's what gets marked. Catalog rows are left
    unmarked and undeleted by a wipe: once real, a movie/show/book/game is
    just catalog data, seeded or not.
    """
    for table in _TRACKER_TABLES:
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.add_column(
                sa.Column(
                    'is_seed_data',
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.false(),
                )
            )
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.alter_column('is_seed_data', server_default=None)


def downgrade() -> None:
    """Downgrade schema."""
    for table in _TRACKER_TABLES:
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.drop_column('is_seed_data')
