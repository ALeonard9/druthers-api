"""Add per-user shelf preferences

Nullable columns retain the original order and enabled state for every
existing account. The preferences endpoint supplies those defaults on read.

Revision ID: e05c3cd4b323
Revises: ab29c4e81f70
Create Date: 2026-08-11 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'e05c3cd4b323'
down_revision: Union[str, Sequence[str], None] = 'ab29c4e81f70'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add nullable account-owned shelf presentation preferences."""
    op.add_column('users', sa.Column('shelf_order', sa.JSON(), nullable=True))
    op.add_column('users', sa.Column('enabled_shelves', sa.JSON(), nullable=True))


def downgrade() -> None:
    """Remove shelf presentation preferences."""
    op.drop_column('users', 'enabled_shelves')
    op.drop_column('users', 'shelf_order')
