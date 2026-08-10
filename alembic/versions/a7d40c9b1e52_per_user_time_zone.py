"""Per-user time zone

Nullable on purpose: NULL means "never chosen" and is read as the
deployment's TIME_ZONE (#322), so existing rows keep behaving exactly as
they did before this column existed.

Revision ID: a7d40c9b1e52
Revises: dbe0e1d4fd86
Create Date: 2026-08-09 22:20:11.004312

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'a7d40c9b1e52'
down_revision: Union[str, Sequence[str], None] = 'dbe0e1d4fd86'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('users', sa.Column('time_zone', sa.String(length=64), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('users', 'time_zone')
