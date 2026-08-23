"""Add users.apple_sub for Sign in with Apple.

Revision ID: a3f7c21e58b9
Revises: f342a9b7c1d0
Create Date: 2026-08-22 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'a3f7c21e58b9'
down_revision: Union[str, Sequence[str], None] = 'f342a9b7c1d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Nullable with a unique index rather than a unique constraint on a
    # populated column: every existing row gets NULL, and NULLs do not collide
    # under a unique index in Postgres or SQLite, so no backfill is needed.
    op.add_column('users', sa.Column('apple_sub', sa.String(), nullable=True))
    op.create_index(op.f('ix_users_apple_sub'), 'users', ['apple_sub'], unique=True)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_users_apple_sub'), table_name='users')
    op.drop_column('users', 'apple_sub')
