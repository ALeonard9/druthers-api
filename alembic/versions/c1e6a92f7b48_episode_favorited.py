"""episode favorited

Favorite an episode independent of watched status (#262): a standout episode
flag, separate from the watch mark. Mirrors watched/watched_at.

Revision ID: c1e6a92f7b48
Revises: b4c8d21e9f57
Create Date: 2026-08-02 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'c1e6a92f7b48'
down_revision: Union[str, Sequence[str], None] = 'b4c8d21e9f57'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('user_tv_episodes', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                'favorited',
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch_op.add_column(sa.Column('favorited_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('user_tv_episodes', schema=None) as batch_op:
        batch_op.drop_column('favorited_at')
        batch_op.drop_column('favorited')
