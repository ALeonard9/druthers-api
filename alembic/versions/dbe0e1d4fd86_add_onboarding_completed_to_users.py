"""Add onboarding_completed to users

Revision ID: dbe0e1d4fd86
Revises: f6a31c8d9e42
Create Date: 2026-08-06 12:05:56.141707

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'dbe0e1d4fd86'
down_revision: Union[str, Sequence[str], None] = 'f6a31c8d9e42'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'users',
        sa.Column(
            'onboarding_completed',
            sa.Boolean(),
            nullable=False,
            server_default=sa.text('false'),
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('users', 'onboarding_completed')
