"""watchlist visibility flags

Opt-in per-category watchlist visibility (#236), independent of the
existing public_* ranked-list flags. NULL flags read as private.

Revision ID: 5361d31e8e2b
Revises: df0fd3ee8b03
Create Date: 2026-08-02 21:51:38.290968

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '5361d31e8e2b'
down_revision: Union[str, Sequence[str], None] = 'df0fd3ee8b03'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('public_watchlist_movies', sa.Boolean(), nullable=True)
        )
        batch_op.add_column(
            sa.Column('public_watchlist_tv', sa.Boolean(), nullable=True)
        )
        batch_op.add_column(
            sa.Column('public_watchlist_books', sa.Boolean(), nullable=True)
        )
        batch_op.add_column(
            sa.Column('public_watchlist_games', sa.Boolean(), nullable=True)
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('public_watchlist_games')
        batch_op.drop_column('public_watchlist_books')
        batch_op.drop_column('public_watchlist_tv')
        batch_op.drop_column('public_watchlist_movies')
